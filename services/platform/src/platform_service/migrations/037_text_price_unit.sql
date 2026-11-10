-- Console text prices are entered as points per million tokens, but were stored as points per
-- token: one million times too high, so every call was refused before any credit moved. Append a
-- corrected price (the current one divided by 1,000,000) for each console-managed text model whose
-- current rate came from the console form. Earlier prices stay in the append-only history.
WITH current AS (
    SELECT m.id,
           COALESCE(
               (SELECT p.rate FROM model_prices p
                WHERE p.kind = 'execution' AND p.model_id = m.id AND p.effective_at <= clock_timestamp()
                ORDER BY p.effective_at DESC, p.created_at DESC LIMIT 1),
               m.definition->'rate') AS rate
    FROM execution_models m
    WHERE m.definition->>'verification_ref' LIKE 'console-managed:%'
      AND m.definition->>'capability' = 'text'
), parts AS (
    SELECT id, rate->>'version' AS version,
           (rate->'input'->>'numerator')::numeric AS input_n,
           (rate->'input'->>'denominator')::numeric * 1000000 AS input_d,
           (rate->'output'->>'numerator')::numeric AS output_n,
           (rate->'output'->>'denominator')::numeric * 1000000 AS output_d
    FROM current
    WHERE rate->>'version' ~ '^(console|price)-[0-9a-f]{12}$'
)
INSERT INTO model_prices(id, kind, model_id, rate, reason, operator_id)
SELECT md5('text-price-unit-037:' || id),
       'execution',
       id,
       jsonb_build_object(
           'version', 'unit-fix-' || substr(md5(id || ':' || version), 1, 12),
           'input', jsonb_build_object('numerator', div(input_n, gcd(input_n, input_d))::text,
                                       'denominator', div(input_d, gcd(input_n, input_d))::text),
           'output', jsonb_build_object('numerator', div(output_n, gcd(output_n, output_d))::text,
                                        'denominator', div(output_d, gcd(output_n, output_d))::text)),
       '文本价格单位更正：后台填写的是每百万 token 的点数',
       'migration-037-text-price-unit'
FROM parts;
