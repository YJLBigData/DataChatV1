package com.feihe.datacode.model;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public class ValidationResult {
    private boolean valid;
    private List<String> errors = new ArrayList<>();
    private List<String> warnings = new ArrayList<>();
    private String normalizedSql = "";

    public ValidationResult() {
    }

    public ValidationResult(boolean valid, List<String> errors, List<String> warnings, String normalizedSql) {
        this.valid = valid;
        this.errors = errors;
        this.warnings = warnings;
        this.normalizedSql = normalizedSql;
    }

    public Map<String, Object> toPayload() {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("valid", valid);
        payload.put("errors", errors);
        payload.put("warnings", warnings);
        payload.put("normalized_sql", normalizedSql);
        return payload;
    }

    public boolean isValid() {
        return valid;
    }

    public void setValid(boolean valid) {
        this.valid = valid;
    }

    public List<String> getErrors() {
        return errors;
    }

    public void setErrors(List<String> errors) {
        this.errors = errors;
    }

    public List<String> getWarnings() {
        return warnings;
    }

    public void setWarnings(List<String> warnings) {
        this.warnings = warnings;
    }

    public String getNormalizedSql() {
        return normalizedSql;
    }

    public void setNormalizedSql(String normalizedSql) {
        this.normalizedSql = normalizedSql;
    }
}
