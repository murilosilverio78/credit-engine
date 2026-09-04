"""
broadfactor_client.py — Cliente da API AntecipaGov / Broadfactor.

Destino sugerido: backend/app/integrations/broadfactor/client.py

Este cliente foi escrito contra o COMPORTAMENTO REAL da API, observado em
sonda de 133 chamadas sobre 8 cotacoes do ambiente dev (31/08/2026), e NAO
contra a documentacao — que diverge em 18 pontos catalogados abaixo.

DIVERGENCIAS ENCODADAS (cada uma tem defesa explicita no codigo):
 1. Auth e HTTP Basic, nao headers client_id/client_secret (doc erra; da 400)
 2. Resposta do auth traz expiracaoMs (milissegundos), nao expiracao (segundos)
 3. refreshToken e JWT, nao UUID
 4. Campo 'valor' NAO existe em /cotacoes, apesar de documentado
 5. Formato de data varia POR CAMPO: dd/MM/yyyy em /cotacoes, ISO em /basico
 6. Enum 'tipo' tem valores nao documentados: 'CONTRATO/EMPENHO' e '-'
 7. Enum 'unidade' vem em ingles: 'HEAD_OFFICE'
 8. cotacaoId = C-<epoch_ms>, EXCETO registros legados (C-0002020...)
 9. Envelope de erro de negocio: {status, hour, serproMessage, customMessage}
10. Toda rota exige prefixo /integracao/; sem ele vem 403 com envelope
    DIFERENTE (Spring Security: {timestamp,status,error,path})
11. /empresa/{cnpj}/* responde 200 com CORPO VAZIO — nao 404. Silencioso.
12. /documentos responde 403 apenas para registros legados (C-0002020...)
13. /contrato/{id}/historico e /empresa/historico/{id} respondem 200 com []
14. customMessage='THERE_IS_NO_FILE_YET' para cotacao sem contrato gerado
15. /proposta (singular) existe; /propostas (plural) da 404
16. /documentos e {documentosEmpresa, documentosSocios[{nomeSocio,documentos}]}
17. /recebido usa campos em INGLES (value, nameOrganization) — resto e portugues
18. ENDPOINTS FANTASMA na spec, retornam 'No static resource':
    /operacao/historico/{cnpj}, /empresa/baixar/{id},
    /cotacoes/{id}/contrato/baixar

ARMADILHAS DE DADO (observadas em producao de dev):
  - cnpjFornecedor pode conter CPF (11 digitos) para cedente pessoa fisica
  - codigoBanco ('033') e numeroContrato ('000022022') tem zero a esquerda:
    SEMPRE string, nunca int
  - /contato e array heterogeneo: campos telefone/email sao opcionais por item
  - /atividades nao marca qual CNAE e o principal
  - /socios nao traz percentual de participacao
  - CEP vem formatado com ponto: '68.371-043'
"""

from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any

import httpx
import structlog

from app.core.config import settings

logger = structlog.get_logger()

API_PREFIX = "/integracao"
DEFAULT_TIMEOUT = 30
TOKEN_SKEW_S = 120          # renova o token 2 min antes de expirar
MAX_RETRIES = 3


# --------------------------------------------------------------------------
# Resultado em tres estados
# --------------------------------------------------------------------------
class Outcome(str, Enum):
    """
    2xx NAO significa sucesso nesta API.

    /empresa/{cnpj}/basico devolve 200 com corpo vazio, e os endpoints de
    historico devolvem 200 com []. Um pipeline que trate 2xx como sucesso
    marca o componente como 'completed' carregando dado nulo adiante.
    """
    OK = "ok"                # 2xx com payload util
    EMPTY = "empty"          # 2xx sem conteudo — sucesso sintatico, vazio semantico
    NOT_FOUND = "not_found"  # 404 de negocio (ex.: THERE_IS_NO_FILE_YET)
    NO_ROUTE = "no_route"    # 404 do Spring: rota inexistente (endpoint fantasma)
    FORBIDDEN = "forbidden"  # 403 (ex.: /documentos em registro legado)
    ERROR = "error"          # demais falhas


@dataclass
class Result:
    outcome: Outcome
    data: Any = None
    status: int | None = None
    message: str | None = None
    endpoint: str = ""

    @property
    def ok(self) -> bool:
        """True apenas com payload util. EMPTY nao e sucesso."""
        return self.outcome is Outcome.OK

    @property
    def usable(self) -> bool:
        """True quando a chamada nao falhou, mesmo sem dado."""
        return self.outcome in (Outcome.OK, Outcome.EMPTY)

    def unwrap(self, default=None):
        return self.data if self.ok else default


class BroadfactorError(RuntimeError):
    def __init__(self, msg, status=None, endpoint="", payload=None):
        super().__init__(msg)
        self.status = status
        self.endpoint = endpoint
        self.payload = payload


class AuthError(BroadfactorError):
    pass


# --------------------------------------------------------------------------
# Parsers defensivos
# --------------------------------------------------------------------------
_RX_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}")
_RX_BR = re.compile(r"^\d{2}/\d{2}/\d{4}$")
_RX_BR_CURTO = re.compile(r"^\d{2}/\d{2}/\d{2}$")


def parse_data(valor: str | None) -> date | None:
    """
    A API mistura formatos POR CAMPO — /cotacoes usa dd/MM/yyyy e /basico usa
    ISO. Um parser global quebra. Este detecta pelo formato do proprio valor,
    entao serve para qualquer campo sem precisar saber de qual endpoint veio.
    """
    if not valor or not isinstance(valor, str):
        return None
    v = valor.strip()
    try:
        if _RX_ISO.match(v):
            return datetime.strptime(v[:10], "%Y-%m-%d").date()
        if _RX_BR.match(v):
            return datetime.strptime(v, "%d/%m/%Y").date()
        if _RX_BR_CURTO.match(v):
            return datetime.strptime(v, "%d/%m/%y").date()
    except ValueError:
        logger.warning("broadfactor.invalid_date", value=valor)
    return None


def so_digitos(v: str | None) -> str:
    return re.sub(r"\D", "", v or "")


@dataclass
class Documento:
    """
    cnpjFornecedor pode conter CPF de 11 digitos (cedente pessoa fisica).
    Extrair 'raiz de CNPJ' de um CPF produz lixo silencioso — por isso o tipo
    e resolvido pelo COMPRIMENTO antes de qualquer operacao.
    """
    numero: str
    tipo: str          # 'CNPJ' | 'CPF' | 'INDEFINIDO'

    @classmethod
    def de(cls, bruto: str | None) -> "Documento":
        d = so_digitos(bruto)
        if len(d) == 14:
            return cls(d, "CNPJ")
        if len(d) == 11:
            return cls(d, "CPF")
        return cls(d, "INDEFINIDO")

    @property
    def e_pj(self) -> bool:
        return self.tipo == "CNPJ"

    @property
    def raiz(self) -> str | None:
        """Raiz de 8 digitos — SOMENTE para CNPJ. None para CPF."""
        return self.numero[:8] if self.e_pj else None

    def mesma_raiz(self, outro: str | None) -> bool:
        o = Documento.de(outro)
        return bool(self.raiz and o.raiz and self.raiz == o.raiz)

    def formatado(self) -> str:
        n = self.numero
        if self.tipo == "CNPJ":
            return f"{n[:2]}.{n[2:5]}.{n[5:8]}/{n[8:12]}-{n[12:]}"
        if self.tipo == "CPF":
            return f"{n[:3]}.{n[3:6]}.{n[6:9]}-{n[9:]}"
        return n


@dataclass
class Cotacao:
    id: str
    nome_fornecedor: str
    documento: Documento
    valor: float                    # valor solicitado pelo cedente
    data: date | None
    data_expiracao: date | None
    margem_disponivel: float
    tipo: str                       # string aberta — enum nao documentado
    bruto: dict = field(repr=False, default_factory=dict)

    # A plataforma aplica cap de 70% sobre o saldo vincendo. Confirmado em 7
    # contratos com diferenca de zero centavo e confirmado pela Broadfactor.
    # Se esse percentual mudar, a derivacao de saldo_vincendo quebra em
    # silencio — validar contra o valor global extraido do PDF do contrato.
    PCT_CAP_PLATAFORMA = 0.70

    @classmethod
    def de_json(cls, d: dict) -> "Cotacao":
        return cls(
            id=d.get("id", ""),
            nome_fornecedor=d.get("nomeFornecedor", ""),
            documento=Documento.de(d.get("cnpjFornecedor")),
            # 'valor' esteve ausente e chegou a se chamar 'valorCotacao' em
            # 03/09/2026. Fallback defensivo enquanto o nome nao estabiliza.
            valor=float(d.get("valor") or d.get("valorCotacao") or 0),
            data=parse_data(d.get("data")),
            data_expiracao=parse_data(d.get("dataExpiracao")),
            margem_disponivel=float(d.get("margemDisponivel") or 0),
            tipo=(d.get("tipo") or "").strip(),
            bruto=d,
        )

    @property
    def saldo_vincendo(self) -> float:
        """Saldo vincendo bruto, derivado da margem (que ja tem o cap de 70%)."""
        return round(self.margem_disponivel / self.PCT_CAP_PLATAFORMA, 2)

    def enquadrar(self, pct_max_contrato: float) -> float:
        """
        Politica conservadora: nunca oferta acima do que o cedente pediu.

        valor_enquadrado = min(valor_solicitado, saldo_vincendo * pct)
        """
        teto = round(self.saldo_vincendo * pct_max_contrato, 2)
        return round(min(self.valor, teto), 2) if self.valor > 0 else teto

    @property
    def vigente(self) -> bool:
        return bool(self.data_expiracao and self.data_expiracao >= date.today())

    @property
    def legado(self) -> bool:
        """
        Registros legados (C-0002020...) nao seguem C-<epoch_ms> e sofrem
        restricoes: /documentos devolve 403 e /contratos devolve 404.
        """
        return self.criado_em() is None

    def criado_em(self) -> datetime | None:
        """
        Deriva a criacao do proprio id (C-<epoch_ms>). None em registros legados.

        Cuidado: ids legados como 'C-0002020163943' TAMBEM tem 13 digitos e
        casam com o regex, mas decodificam para 1970. Por isso a data e
        validada contra uma janela plausivel antes de ser aceita.
        """
        m = re.match(r"^C-(\d{13})$", self.id)
        if not m:
            return None
        try:
            dt = datetime.fromtimestamp(int(m.group(1)) / 1000)
        except (ValueError, OSError, OverflowError):
            return None
        return dt if dt.year >= 2015 else None

    @property
    def contrato_like(self) -> bool:
        """Cobre 'CONTRATO' e o valor nao documentado 'CONTRATO/EMPENHO'."""
        return "CONTRATO" in self.tipo.upper()


@dataclass
class EmpresaBasico:
    nome_fantasia: str
    documento: Documento
    capital_social: float
    faturamento_anual: float
    data_abertura: date | None
    natureza_juridica: str
    regime: str
    unidade: str                    # 'HEAD_OFFICE' — enum em ingles

    @classmethod
    def de_json(cls, d: dict) -> "EmpresaBasico":
        return cls(
            nome_fantasia=d.get("nomeFantasia", ""),
            documento=Documento.de(d.get("cnpj")),
            capital_social=float(d.get("capitalSocial") or 0),
            faturamento_anual=float(d.get("faturamentoAnual") or 0),
            data_abertura=parse_data(d.get("dataAbertura")),
            natureza_juridica=d.get("naturezaJuridica", ""),
            regime=(d.get("regime") or "").strip(),
            unidade=(d.get("unidade") or "").strip(),
        )

    @property
    def e_matriz(self) -> bool:
        """A API usa ingles. Aceita variantes por seguranca."""
        return self.unidade.upper() in ("HEAD_OFFICE", "MATRIZ")

    @property
    def risco_desenquadramento_simples(self) -> bool:
        """
        Teto do Simples Nacional e R$ 4,8 mi. Cedente acima de 80% do teto
        pode desenquadrar durante a vigencia do contrato que lastreia a
        operacao — carga tributaria sobe e a margem comprime justamente no
        periodo de exposicao.
        """
        return (self.regime.upper() == "SIMPLES_NACIONAL"
                and self.faturamento_anual > 3_840_000)


@dataclass
class Socio:
    nome: str
    documento: Documento
    telefone: str | None
    qualificacao: str
    # A API nao expoe percentual de participacao nem data de entrada.

    @classmethod
    def de_json(cls, d: dict) -> "Socio":
        return cls(
            nome=d.get("nome", ""),
            documento=Documento.de(d.get("cpf")),
            telefone=d.get("telefone"),
            qualificacao=d.get("qualificacao", ""),
        )


@dataclass
class ContaBancaria:
    codigo_banco: str      # '033' — zero a esquerda, jamais int
    nome_banco: str
    agencia: str
    conta: str             # '13000871-3' — inclui digito verificador

    @classmethod
    def de_json(cls, d: dict) -> "ContaBancaria":
        return cls(
            codigo_banco=str(d.get("codigoBanco") or "").strip(),
            nome_banco=d.get("nomeBanco", ""),
            agencia=str(d.get("agencia") or "").strip(),
            conta=str(d.get("conta") or "").strip(),
        )


@dataclass
class Cnae:
    codigo: str
    descricao: str
    # A API NAO indica qual e o principal. Ver Fornecedor.cnae_principal.


@dataclass
class Contato:
    """Array heterogeneo: telefone e email aparecem em itens distintos."""
    nome: str | None = None
    telefone: str | None = None
    email: str | None = None


@dataclass
class ArquivoContrato:
    arquivo: str
    numero_contrato: str   # '000022022' — zero a esquerda, sempre string


@dataclass
class DocumentoAnexo:
    tipo: str              # CNPJ | CPF | MEI | FATURAMENTO | EXTRATO_SIMPLES...
    arquivo: str
    id: str | None = None
    dono: str = "EMPRESA"  # 'EMPRESA' ou nome do socio


@dataclass
class Recebimento:
    """Campos em INGLES neste endpoint, ao contrario do resto da API."""
    valor: float
    orgao: str
    codigo_orgao: str | None
    orgao_superior: str | None
    unidade_gestora: str | None
    competencia: str | None
    acao: str | None = None

    @classmethod
    def de_json(cls, d: dict) -> "Recebimento":
        return cls(
            valor=float(d.get("value") or 0),
            orgao=d.get("nameOrganization") or "",
            codigo_orgao=d.get("organizationCode"),
            orgao_superior=d.get("nameSuperiorOrganization"),
            unidade_gestora=d.get("nameUG"),
            competencia=d.get("competency"),
        )


# --------------------------------------------------------------------------
# Concentracao de sacado
# --------------------------------------------------------------------------
@dataclass
class ConcentracaoSacado:
    """
    Metrica derivada de /recebido: mede dependencia do cedente em relacao a
    poucos orgaos pagadores.

    Na amostra de dev, dois cedentes com margem semelhante apresentaram HHI
    de 1.180 (nove orgaos) e 10.000 (um unico orgao). O segundo depende
    integralmente de um ministerio: contingenciamento naquele orgao trava o
    fluxo inteiro que lastreia a operacao. E sinal de dilucao mais direto que
    faturamento declarado, e nao exige contrato de bureau.
    """
    hhi: float                      # 0–10.000
    n_orgaos: int
    top_orgao: str
    top_participacao: float         # 0–1
    total: float
    amostra_parcial: bool           # True se ha mais paginas alem da lida

    @property
    def faixa(self) -> str:
        if self.hhi < 2500:
            return "PULVERIZADO"
        if self.hhi <= 6000:
            return "MODERADO"
        return "CONCENTRADO"

    @classmethod
    def calcular(cls, recebimentos: list[Recebimento],
                 total_elements: int | None = None) -> "ConcentracaoSacado | None":
        if not recebimentos:
            return None
        por_orgao: dict[str, float] = {}
        for r in recebimentos:
            por_orgao[r.orgao] = por_orgao.get(r.orgao, 0.0) + r.valor
        total = sum(por_orgao.values())
        if total <= 0:
            return None
        shares = {k: v / total for k, v in por_orgao.items()}
        top = max(shares.items(), key=lambda x: x[1])
        return cls(
            hhi=round(sum(s * s for s in shares.values()) * 10_000, 1),
            n_orgaos=len(por_orgao),
            top_orgao=top[0],
            top_participacao=round(top[1], 4),
            total=round(total, 2),
            amostra_parcial=bool(total_elements and total_elements > len(recebimentos)),
        )


@dataclass
class Proposta:
    """
    Retorno de /proposta. O envio traz 13 campos; o retorno devolve 20 —
    os 7 extras identificam a proposta e ecoam o cedente.

    status observado: 'SENT' (INGLES, apesar de a doc prever
    INTEGRANDO/ENVIADA/ANALISE/APROVADA/REPROVADA em portugues).
    Tratado como string aberta.

    Nao ha campo de motivo de recusa, parecer ou observacao em nenhum ponto
    do ciclo — o parecer tecnico da Broadfactor nao trafega pela API.
    """
    proposta_id: str
    cotacao_id: str
    status: str
    valor_emprestimo: float
    valor_liquido: float
    numero_parcelas: int
    valor_parcela: float
    taxa_juros: float          # % MENSAL
    cet: float                 # % MENSAL
    criado_em: str | None
    cnpj_fornecedor: Documento | None
    nome_fornecedor: str | None
    bruto: dict = field(repr=False, default_factory=dict)

    @classmethod
    def de_json(cls, d: dict) -> "Proposta":
        return cls(
            proposta_id=d.get("propostaId", ""),
            cotacao_id=d.get("cotacaoId", ""),
            status=(d.get("status") or "").strip(),
            valor_emprestimo=float(d.get("valorEmprestimo") or 0),
            valor_liquido=float(d.get("valorLiquido") or 0),
            numero_parcelas=int(d.get("numeroParcelas") or 0),
            valor_parcela=float(d.get("valorParcela") or 0),
            taxa_juros=float(d.get("taxaJuros") or 0),
            cet=float(d.get("cet") or 0),
            criado_em=d.get("criadoEm"),
            cnpj_fornecedor=(Documento.de(d.get("cnpjFornecedorCotacao"))
                             if d.get("cnpjFornecedorCotacao") else None),
            nome_fornecedor=d.get("nomeFornecedorCotacao"),
            bruto=d,
        )

    @property
    def bullet(self) -> bool:
        return self.numero_parcelas == 1


@dataclass
class Fornecedor:
    """Visao consolidada do cedente para uma cotacao."""
    cotacao_id: str
    basico: EmpresaBasico | None = None
    socios: list[Socio] = field(default_factory=list)
    contas: list[ContaBancaria] = field(default_factory=list)
    cnaes: list[Cnae] = field(default_factory=list)
    contatos: list[Contato] = field(default_factory=list)
    endereco: dict = field(default_factory=dict)
    documentos: list[DocumentoAnexo] = field(default_factory=list)
    recebimentos: list[Recebimento] = field(default_factory=list)
    concentracao: ConcentracaoSacado | None = None
    lacunas: list[str] = field(default_factory=list)   # o que nao veio

    @property
    def cnae_principal(self) -> Cnae | None:
        """
        A API nao marca o principal; assume-se a posicao 0 por convencao.
        Para uso em scorecard, cruzar com a BrasilAPI, que separa
        explicitamente principal de secundarios.
        """
        return self.cnaes[0] if self.cnaes else None

    @property
    def tem_scr(self) -> bool:
        """
        ENDIVIDAMENTO_EMPRESA e o relatorio SCR/Bacen. Aparece no exemplo da
        documentacao, mas NAO ocorreu em nenhuma das 8 cotacoes sondadas.
        Trate como possivel, nunca como esperado.
        """
        return any(d.tipo == "ENDIVIDAMENTO_EMPRESA" for d in self.documentos)


# --------------------------------------------------------------------------
# Cliente
# --------------------------------------------------------------------------
class BroadfactorClient:
    def __init__(self, client_id: str | None = None,
                 client_secret: str | None = None,
                 base_url: str | None = None,
                 timeout: int = DEFAULT_TIMEOUT):
        client_id = client_id or settings.BROADFACTOR_CLIENT_ID
        client_secret = client_secret or settings.BROADFACTOR_CLIENT_SECRET
        base_url = base_url or settings.BROADFACTOR_BASE_URL
        if not client_id or not client_secret:
            raise AuthError("client_id e client_secret sao obrigatorios")
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._token: str | None = None
        self._expira_em: float = 0.0
        self._claims: dict = {}
        self._s = httpx.Client(
            timeout=self.timeout,
            verify=settings.HTTPX_VERIFY_SSL,
        )

    # ------------------------------------------------------------ auth
    def _autenticar(self) -> None:
        url = f"{self.base_url}{API_PREFIX}/autenticar/token"
        # HTTP Basic. A documentacao manda headers client_id/client_secret,
        # o que retorna 400 'Required request header Authorization ... not present'.
        r = self._s.post(url, auth=(self.client_id, self.client_secret),
                         timeout=self.timeout)
        if r.status_code != 200:
            raise AuthError(f"falha de autenticacao: HTTP {r.status_code}",
                            status=r.status_code, endpoint=url)
        j = r.json()
        self._token = j["token"]
        # expiracaoMs, em milissegundos — nao 'expiracao' em segundos
        ttl_ms = j.get("expiracaoMs") or 3_600_000
        self._expira_em = time.time() + (ttl_ms / 1000) - TOKEN_SKEW_S
        self._claims = self._decodificar_jwt(self._token) or {}
        logger.info(
            "broadfactor.authenticated",
            tenant=self.tenant.get("companyName"),
        )

    @staticmethod
    def _decodificar_jwt(tok: str) -> dict | None:
        try:
            p = tok.split(".")[1]
            p += "=" * (-len(p) % 4)
            return json.loads(base64.urlsafe_b64decode(p))
        except Exception:
            return None

    @property
    def tenant(self) -> dict:
        if not self._claims:
            self._garantir_token()
        return self._claims.get("tenant", {})

    def _garantir_token(self) -> None:
        if not self._token or time.time() >= self._expira_em:
            self._autenticar()

    # ------------------------------------------------------------ transporte
    def _req(self, method: str, caminho: str, *, body=None) -> Result:
        self._garantir_token()
        # Prefixo forcado: sem /integracao a API devolve 403 com envelope do
        # Spring Security, que parece erro de autorizacao mas e rota errada.
        if not caminho.startswith(API_PREFIX):
            caminho = f"{API_PREFIX}{caminho}"
        url = f"{self.base_url}{caminho}"

        ultimo_erro = None
        for tentativa in range(1, MAX_RETRIES + 1):
            try:
                r = self._s.request(
                    method, url, json=body, timeout=self.timeout,
                    headers={"Authorization": f"Bearer {self._token}"})
            except httpx.RequestError as e:
                ultimo_erro = e
                if tentativa < MAX_RETRIES:
                    time.sleep(2 ** tentativa)
                    continue
                return Result(Outcome.ERROR, message=str(e), endpoint=caminho)

            if r.status_code == 401 and tentativa < MAX_RETRIES:
                self._autenticar()   # token expirado no meio do voo
                continue
            if r.status_code >= 500 and tentativa < MAX_RETRIES:
                time.sleep(2 ** tentativa)
                continue
            return self._interpretar(r, caminho)

        return Result(Outcome.ERROR, message=str(ultimo_erro), endpoint=caminho)

    def _interpretar(self, r: httpx.Response, caminho: str) -> Result:
        try:
            payload = r.json() if "json" in r.headers.get("content-type", "") else None
        except ValueError:
            payload = None

        # A API tem DOIS envelopes de erro distintos:
        #   negocio -> {status, hour, serproMessage, customMessage}
        #   Spring  -> {timestamp, status, error, path}   (rota inexistente)
        msg = None
        if isinstance(payload, dict):
            msg = payload.get("customMessage") or payload.get("error")

        if r.status_code == 403:
            # Ex.: /documentos em registro legado (C-0002020...)
            return Result(Outcome.FORBIDDEN, status=403, message=msg, endpoint=caminho)

        if r.status_code == 404:
            if msg and "No static resource" in str(msg):
                # Endpoint fantasma — consta na spec mas nao existe no servidor
                logger.error("broadfactor.route_not_found", endpoint=caminho)
                return Result(Outcome.NO_ROUTE, status=404, message=msg, endpoint=caminho)
            # 404 de negocio, ex.: THERE_IS_NO_FILE_YET
            return Result(Outcome.NOT_FOUND, status=404, message=msg, endpoint=caminho)

        if r.status_code >= 400:
            return Result(Outcome.ERROR, status=r.status_code, message=msg,
                          endpoint=caminho)

        # 2xx: distinguir payload util de corpo vazio.
        # /empresa/{cnpj}/* responde 200 vazio; historicos respondem 200 [].
        vazio = (
            payload is None
            or payload == {} or payload == []
            or (isinstance(r.content, bytes) and len(r.content.strip()) == 0)
        )
        if vazio:
            logger.debug("broadfactor.empty_response", endpoint=caminho)
            return Result(Outcome.EMPTY, data=payload, status=r.status_code,
                          endpoint=caminho)
        return Result(Outcome.OK, data=payload, status=r.status_code, endpoint=caminho)

    # ------------------------------------------------------------ cotacoes
    def listar_cotacoes(self) -> list[Cotacao]:
        res = self._req("GET", "/cotacoes")
        if not res.ok:
            return []
        cotacoes: list[Cotacao] = []
        for payload in res.data:
            if not isinstance(payload, dict):
                continue
            try:
                cotacoes.append(Cotacao.de_json(payload))
            except (TypeError, ValueError) as exc:
                logger.warning(
                    "broadfactor.quote_parse_failed",
                    cotacao_id=payload.get("id"),
                    error=str(exc),
                )
        return cotacoes

    def cotacoes_elegiveis(self, *,
                           pct_max_contrato: float,
                           ticket_minimo: float,
                           ticket_maximo: float,
                           dias_minimos_expiracao: int = 5,
                           somente_vigentes: bool = True,
                           somente_contrato: bool = True,
                           somente_pj: bool = True) -> list[Cotacao]:
        """
        Filtro duro do estagio S0. Roda sobre a listagem, sem chamada extra.

        Piso e teto incidem sobre o VALOR ENQUADRADO, nunca sobre a margem nem
        sobre o valor solicitado bruto. Filtrar pela margem erra em 9% a 15%
        dos casos: um cedente com margem de R$ 55 mi pedindo R$ 1 mi seria
        descartado por um teto, e outro com margem de R$ 1,2 mi pedindo
        R$ 63 mil passaria num piso — ambos pela grandeza errada.

        Aplicar o piso sobre o valor enquadrado tambem evita que a operacao
        entre no funil e morra depois, ja tendo consumido consulta paga.

        somente_vigentes=False e util no ambiente de dev, onde a massa pode
        estar com datas vencidas.

        Retorna apenas as aprovadas. Use `triar` para obter tambem os motivos
        de descarte, que precisam ser registrados para calibrar os parametros.
        """
        aprovadas, _ = self.triar(
            pct_max_contrato=pct_max_contrato,
            ticket_minimo=ticket_minimo,
            ticket_maximo=ticket_maximo,
            dias_minimos_expiracao=dias_minimos_expiracao,
            somente_vigentes=somente_vigentes,
            somente_contrato=somente_contrato,
            somente_pj=somente_pj,
        )
        return aprovadas

    def triar(self, *,
              pct_max_contrato: float,
              ticket_minimo: float,
              ticket_maximo: float,
              dias_minimos_expiracao: int = 5,
              somente_vigentes: bool = True,
              somente_contrato: bool = True,
              somente_pj: bool = True) -> tuple[list[Cotacao], list[tuple[Cotacao, str]]]:
        """
        Igual a cotacoes_elegiveis, mas devolve (aprovadas, descartadas).

        Cada descarte vem com o motivo. Sem esse registro nao ha como calibrar
        ticket_minimo e ticket_maximo depois — e como o teto descarta em
        definitivo, a cotacao desaparece sem deixar rastro.
        """
        limite_data = date.today() + timedelta(days=dias_minimos_expiracao)
        aprovadas: list[Cotacao] = []
        descartadas: list[tuple[Cotacao, str]] = []

        for c in self.listar_cotacoes():
            if somente_contrato and not c.contrato_like:
                descartadas.append((c, f"tipo_nao_elegivel:{c.tipo or 'vazio'}"))
                continue
            if somente_pj and not c.documento.e_pj:
                descartadas.append((c, f"cedente_nao_pj:{c.documento.tipo}"))
                continue
            if somente_vigentes and (
                not c.data_expiracao or c.data_expiracao < limite_data
            ):
                descartadas.append((c, "janela_expiracao_insuficiente"))
                continue

            enquadrado = c.enquadrar(pct_max_contrato)
            if enquadrado < ticket_minimo:
                descartadas.append((c, "abaixo_ticket_minimo"))
                continue
            if enquadrado > ticket_maximo:
                descartadas.append((c, "acima_ticket_maximo"))
                continue
            aprovadas.append(c)

        aprovadas.sort(key=lambda c: -c.enquadrar(pct_max_contrato))
        return aprovadas, descartadas

    def contratos_da_cotacao(self, cid: str) -> list[ArquivoContrato]:
        res = self._req("GET", f"/cotacoes/{cid}/contratos")
        if res.outcome is Outcome.NOT_FOUND:
            # THERE_IS_NO_FILE_YET — contrato ainda nao gerado. Nao e erro.
            logger.info(
                "broadfactor.contract_not_ready",
                cotacao_id=cid,
                message=res.message,
            )
            return []
        if not res.ok:
            return []
        return [ArquivoContrato(arquivo=d.get("arquivo", ""),
                                numero_contrato=str(d.get("numeroContrato") or ""))
                for d in res.data]

    def baixar_contrato(self, cid: str, numero: str) -> bytes | None:
        """
        Somente base64. O endpoint binario /contrato/baixar consta na spec mas
        NAO existe no servidor (retorna 'No static resource').
        """
        res = self._req("GET", f"/cotacoes/{cid}/contratos/{numero}")
        if not res.ok or not isinstance(res.data, dict):
            return None
        b64 = res.data.get("arquivo")
        if not b64:
            return None
        try:
            return base64.b64decode(b64)
        except Exception:
            logger.error(
                "broadfactor.invalid_contract_base64",
                cotacao_id=cid,
                numero_contrato=numero,
            )
            return None

    # ------------------------------------------------------------ fornecedor
    def _lista(self, caminho: str) -> list[dict]:
        res = self._req("GET", caminho)
        if not res.ok:
            return []
        d = res.data
        if isinstance(d, list):
            return [x for x in d if isinstance(x, dict)]
        return [d] if isinstance(d, dict) else []

    def carregar_fornecedor(self, cid: str, *,
                            paginas_recebido: int = 1,
                            tamanho_pagina: int = 50) -> Fornecedor:
        """
        Consolida o cedente a partir de UMA cotacao.

        Todo o enriquecimento e quote-scoped: /empresa/{cnpj}/* responde 200
        vazio, entao nao ha como consultar um cedente fora do contexto de uma
        cotacao aberta.

        O campo `lacunas` registra o que nao veio — para o gate de integridade
        do pipeline decidir se ha base suficiente para pontuar.
        """
        f = Fornecedor(cotacao_id=cid)

        res = self._req("GET", f"/empresa/{cid}/basico")
        if res.ok:
            f.basico = EmpresaBasico.de_json(res.data)
        else:
            f.lacunas.append(f"basico:{res.outcome.value}")

        f.socios = [Socio.de_json(d) for d in self._lista(f"/empresa/{cid}/socios")]
        if not f.socios:
            f.lacunas.append("socios:vazio")

        f.contas = [ContaBancaria.de_json(d)
                    for d in self._lista(f"/empresa/{cid}/banco")]
        f.cnaes = [Cnae(codigo=str(d.get("codigo") or ""),
                        descricao=d.get("descricao", ""))
                   for d in self._lista(f"/empresa/{cid}/atividades")]

        # Array heterogeneo: cada item traz um subconjunto dos campos.
        f.contatos = [Contato(nome=d.get("nome"), telefone=d.get("telefone"),
                              email=d.get("email"))
                      for d in self._lista(f"/empresa/{cid}/contato")]

        res = self._req("GET", f"/empresa/{cid}/endereco")
        f.endereco = res.data if res.ok and isinstance(res.data, dict) else {}

        f.documentos = self._documentos(cid, f)
        f.recebimentos = self.recebimentos(cid, paginas=paginas_recebido,
                                           tamanho=tamanho_pagina)
        f.concentracao = ConcentracaoSacado.calcular(f.recebimentos)
        if not f.recebimentos:
            f.lacunas.append("recebido:vazio")
        return f

    def _documentos(self, cid: str, f: Fornecedor) -> list[DocumentoAnexo]:
        res = self._req("GET", f"/empresa/{cid}/documentos")
        if res.outcome is Outcome.FORBIDDEN:
            # Observado apenas em registros legados (C-0002020...). Os demais
            # endpoints respondem 200 para os mesmos ids.
            f.lacunas.append("documentos:403_legado")
            return []
        if not res.ok or not isinstance(res.data, dict):
            f.lacunas.append(f"documentos:{res.outcome.value}")
            return []

        # Estrutura ANINHADA — quem assume lista plana perde os docs dos socios.
        out = [DocumentoAnexo(tipo=d.get("tipo", ""), arquivo=d.get("arquivo", ""),
                              id=d.get("id"), dono="EMPRESA")
               for d in (res.data.get("documentosEmpresa") or [])]
        for bloco in (res.data.get("documentosSocios") or []):
            dono = bloco.get("nomeSocio", "SOCIO")
            for d in (bloco.get("documentos") or []):
                out.append(DocumentoAnexo(tipo=d.get("tipo", ""),
                                          arquivo=d.get("arquivo", ""),
                                          id=d.get("id"), dono=dono))
        return out

    def recebimentos(self, cid: str, paginas: int = 1,
                     tamanho: int = 50,
                     exigir_completo: bool = False) -> list[Recebimento]:
        """
        Pagamentos do governo ao cedente. POST com page/size no caminho.
        Campos em ingles, ao contrario do resto da API.
        """
        out: list[Recebimento] = []
        for pagina in range(paginas):
            res = self._req("POST", f"/empresa/recebido/{cid}/{pagina}/{tamanho}",
                            body={})
            if not res.ok or not isinstance(res.data, dict):
                if exigir_completo and res.outcome is not Outcome.EMPTY:
                    raise BroadfactorError(
                        f"falha ao paginar recebimentos: {res.outcome.value}",
                        status=res.status,
                        endpoint=res.endpoint,
                        payload=res.data,
                    )
                break
            conteudo = res.data.get("content") or []
            out.extend(Recebimento.de_json(d) for d in conteudo)
            if pagina + 1 >= (res.data.get("totalPages") or 1):
                break
        else:
            if exigir_completo:
                raise BroadfactorError(
                    f"paginacao de recebimentos excedeu {paginas} paginas",
                    endpoint=f"/empresa/recebido/{cid}",
                )
        return out

    # ------------------------------------------------------------ propostas
    def listar_propostas(self) -> list[dict]:
        """Rota SINGULAR. O plural /propostas retorna 404."""
        res = self._req("GET", "/proposta")
        return res.data if res.ok and isinstance(res.data, list) else []

    def obter_proposta(self, proposta_id: str) -> dict | None:
        res = self._req("GET", f"/proposta/{proposta_id}")
        return res.data if res.ok else None

    def criar_proposta(self, cid: str, proposta: dict, *,
                       margem_disponivel: float | None = None,
                       permitir_duplicada: bool = False) -> Result:
        """
        ATENCAO: este POST e IRREVERSIVEL e NAO E VALIDADO pelo servidor.

        Comportamento confirmado em sandbox (31/08/2026):
          - A proposta nasce direto em status 'SENT'. Nao ha rascunho.
          - Nao existe endpoint de cancelamento ou substituicao.
          - Enviar valorEmprestimo = 3x margemDisponivel foi ACEITO com 200.
          - valorLiquido incoerente com valorEmprestimo foi ACEITO com 200.
          - Segunda proposta na mesma cotacao CRIA outro registro; nao substitui.
        O servidor valida apenas presenca de campo, nunca consistencia.
        Toda a protecao mora aqui.

        taxaJuros e cet sao percentuais MENSAIS.
        Para liquidacao unica no vencimento: numeroParcelas=1 com
        dataPrimeiraParcela == dataUltimaParcela (forma confirmada em sandbox).
        """
        obrigatorios = {"valorEmprestimo", "valorLiquido", "numeroParcelas",
                        "valorParcela", "taxaJuros", "cet", "dataLiberacao",
                        "dataPrimeiraParcela", "dataUltimaParcela"}
        faltando = obrigatorios - set(proposta)
        if faltando:
            raise ValueError(f"campos obrigatorios ausentes: {sorted(faltando)}")

        emprestimo = float(proposta["valorEmprestimo"])
        liquido = float(proposta["valorLiquido"])
        parcela = float(proposta["valorParcela"])
        n = int(proposta["numeroParcelas"])

        if emprestimo <= 0 or n < 1:
            raise ValueError("valorEmprestimo e numeroParcelas devem ser positivos")

        # Teto de cessao — o servidor NAO barra excesso.
        if margem_disponivel is not None and emprestimo > margem_disponivel:
            raise ValueError(
                f"valorEmprestimo {emprestimo:,.2f} excede margemDisponivel "
                f"{margem_disponivel:,.2f} — a API aceitaria, o negocio nao.")

        # Coerencia aritmetica — o servidor NAO checa.
        if liquido > emprestimo:
            raise ValueError("valorLiquido nao pode superar valorEmprestimo")
        total = parcela * n
        if total < emprestimo * 0.99:
            raise ValueError(
                f"parcelas somam {total:,.2f}, abaixo do principal {emprestimo:,.2f}")

        # Idempotencia: nao ha cancelamento, entao duplicata e permanente.
        if not permitir_duplicada:
            existentes = [p for p in self.listar_propostas()
                          if p.get("cotacaoId") == cid]
            if existentes:
                raise ValueError(
                    f"ja existem {len(existentes)} proposta(s) para {cid} "
                    f"(status: {[p.get('status') for p in existentes]}). "
                    f"Nao ha endpoint de cancelamento. "
                    f"Use permitir_duplicada=True se for intencional.")

        return self._req("POST", f"/proposta/criar/{cid}", body=proposta)


__all__ = [
    "BroadfactorClient", "BroadfactorError", "AuthError",
    "Outcome", "Result", "Documento", "Cotacao", "EmpresaBasico", "Socio",
    "ContaBancaria", "Cnae", "Contato", "ArquivoContrato", "DocumentoAnexo",
    "Recebimento", "ConcentracaoSacado", "Proposta", "Fornecedor", "parse_data",
]
