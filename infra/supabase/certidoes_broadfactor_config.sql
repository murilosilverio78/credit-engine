-- PROPOSTA MANUAL: aplicar no SQL Editor antes do deploy.
-- Nao e migration e nao altera politica, score ou componentes.

UPDATE documentos_captura_config
SET tipos_capturados = ARRAY(
  SELECT DISTINCT tipo
  FROM unnest(tipos_capturados || ARRAY[
    'CND_FEDERAL',
    'CND_ESTADUAL',
    'CND_MUNICIPAL'
  ]) AS tipo
  ORDER BY tipo
)
WHERE singleton = TRUE;
