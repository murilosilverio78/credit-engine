"""
Teste standalone: score_engine recalibrado.

Le os outputs dos componentes para simular o pipeline e roda o consolidator
deterministico. Para a calibracao MASF, Porte/Operacionalidade e fixado em
Atencao, o nivel esperado pela chamada Opus com temperature=0.

Uso:
    cd backend
    python tests/test_score_engine.py
"""
import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
for key in (
    "SECRET_KEY",
    "TWOCAPTCHA_API_KEY",
    "SMTP_HOST",
    "SMTP_USER",
    "SMTP_PASS",
):
    os.environ.setdefault(key, "test")
sys.path.insert(0, str(ROOT))

from app.workers.tasks.score_engine import (  # noqa: E402
    NIVEL_NOTA,
    PESOS_MERITO,
    consolidar_score,
)

CNPJ = "03012610000101"


def load_snapshots() -> dict:
    tests_dir = Path(__file__).parent
    snapshots = {}
    components = [
        "brasil_api",
        "pessoa_juridica",
        "contratos",
        "recursos_recebidos",
        "acordos_leniencia",
        "ceis",
        "cnep",
        "cepim",
        "web_research",
    ]
    for component in components:
        output_file = tests_dir / f"output_{component}.json"
        if output_file.exists():
            snapshots[component] = json.loads(output_file.read_text(encoding="utf-8"))
            print(f"Carregado: {component}")
        else:
            print(f"Nao encontrado: {component} (pulando)")
    return snapshots


def forced_porte_atencao() -> dict:
    score = NIVEL_NOTA["Atencao"]
    peso = PESOS_MERITO["porte_operacionalidade"]
    return {
        "score": score,
        "nivel": "Atencao",
        "peso": peso,
        "fatores": [
            "Porte pequeno com contratos relevantes em servicos intensivos em mao de obra.",
        ],
        "relatorio": "Porte/Operacionalidade fixado em Atencao para harness de calibracao MASF.",
        "flags": ["stub_llm_calibracao"],
        "fonte": "llm",
        "score_contrib": round(score * peso, 2),
    }


def main() -> int:
    snapshots = load_snapshots()
    result = consolidar_score(
        CNPJ,
        snapshots,
        operacao={
            "valor_solicitado": 500000,
            "contrato_saldo": 800000,
            "prazo_dias": 180,
        },
        porte_dimension=forced_porte_atencao(),
    )

    print("\n" + "=" * 60)
    print(f"SCORE FINAL: {result['score']}/100")
    print(f"MERIT:       {result['merit']}/100")
    print(f"RATING:      {result['rating']}")
    print(f"FATOR REG:   {result['fator_regularidade']:.2f}")
    print(f"LIMITE:      {result['limite_sugerido_pct_contrato'] * 100:.0f}% do contrato")
    print("=" * 60 + "\n")

    print("Dimensoes:")
    for name, values in result.get("dimensoes", {}).items():
        contrib = values["score"] * values["peso"]
        print(
            f"  {name:35} {values['score']:5.1f} "
            f"{values['nivel']:11} peso={values['peso']:.2f} contrib={contrib:5.1f}"
        )

    print("\nRegularidade:")
    print(json.dumps(result["regularidade"], ensure_ascii=False, indent=2))
    print(f"\nParecer:\n  {result.get('parecer')}")

    out = Path(__file__).parent / "output_score_engine.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nResultado salvo em: {out}")

    if result["rating"] != "B":
        print("ERRO: MASF deveria sair rating B.")
        return 1
    if not (73.5 <= result["score"] <= 75.5):
        print("ERRO: score MASF fora da faixa esperada aproximada de 74,6.")
        return 1
    if "taxa_sugerida_am" in result:
        print("ERRO: score_engine nao deve emitir taxa_sugerida_am.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
