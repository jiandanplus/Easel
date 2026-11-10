-- Some suppliers name models with spaces ("Midjourney v8.2"). Single inner spaces are allowed;
-- other whitespace, control characters and leading, trailing or doubled spaces are not.
ALTER TABLE managed_model_bindings DROP CONSTRAINT managed_model_bindings_upstream_model_check;
ALTER TABLE managed_model_bindings ADD CONSTRAINT managed_model_bindings_upstream_model_check CHECK(
    length(upstream_model) BETWEEN 1 AND 128
    AND replace(upstream_model, ' ', '') !~ '[[:space:][:cntrl:]]'
    AND upstream_model !~ '(^ | $|  )');
