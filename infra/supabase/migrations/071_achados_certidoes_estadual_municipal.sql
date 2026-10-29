-- Achados informativos das certidoes estadual e municipal lidas da Broadfactor.
-- Aplicar manualmente antes de reemitir cadastro_regularidade com o emissor 34.

INSERT INTO finding_catalog (codigo, versao, escopo, classe_padrao, natureza, tipo_valor, descricao) VALUES
  ('certidao_cnd_estadual', 1, 'CEDENTE', 'INFORMATIVO', 'CODIGO', 'OBJETO',
   'Situação e vigência da certidão estadual capturada pela Broadfactor'),
  ('certidao_cnd_municipal', 1, 'CEDENTE', 'INFORMATIVO', 'CODIGO', 'OBJETO',
   'Situação e vigência da certidão municipal capturada pela Broadfactor')
ON CONFLICT (codigo, versao) DO NOTHING;

NOTIFY pgrst, 'reload schema';
