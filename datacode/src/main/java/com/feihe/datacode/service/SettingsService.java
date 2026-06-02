package com.feihe.datacode.service;

import com.feihe.datacode.config.DataCodeProperties;
import org.springframework.stereotype.Service;

import javax.annotation.PostConstruct;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.util.LinkedHashMap;
import java.util.Map;

@Service
public class SettingsService {
    private static final String MODEL_NAME = "model.name";
    private static final String MODEL_API_KEY = "model.api_key";

    private final SqliteStore store;
    private final DataCodeProperties properties;

    public SettingsService(SqliteStore store, DataCodeProperties properties) {
        this.store = store;
        this.properties = properties;
    }

    @PostConstruct
    public void applyPersistedSettings() throws Exception {
        String modelName = getValue(MODEL_NAME);
        String apiKey = getValue(MODEL_API_KEY);
        if (!isBlank(modelName)) {
            properties.getModel().setModelName(modelName.trim());
        }
        if (!isBlank(apiKey)) {
            properties.getModel().setApiKey(apiKey.trim());
        }
    }

    public Map<String, Object> modelSettingsPayload() {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("provider_id", properties.getModel().getProviderId());
        payload.put("model_name", properties.getModel().getModelName());
        payload.put("base_url", properties.getModel().getBaseUrl());
        payload.put("api_key_configured", !isBlank(properties.getModel().getApiKey()));
        payload.put("api_key_masked", maskKey(properties.getModel().getApiKey()));
        return payload;
    }

    public Map<String, Object> updateModelSettings(String modelName, String apiKey, long operatorUserId) throws Exception {
        String cleanModel = modelName == null ? "" : modelName.trim();
        String cleanApiKey = apiKey == null ? "" : apiKey.trim();
        if (cleanModel.isEmpty()) {
            throw new IllegalArgumentException("模型名称不能为空");
        }
        if (cleanApiKey.isEmpty() && isBlank(properties.getModel().getApiKey())) {
            throw new IllegalArgumentException("百炼模型 AK 不能为空");
        }
        properties.getModel().setModelName(cleanModel);
        putValue(MODEL_NAME, cleanModel, operatorUserId);
        if (!cleanApiKey.isEmpty()) {
            properties.getModel().setApiKey(cleanApiKey);
            putValue(MODEL_API_KEY, cleanApiKey, operatorUserId);
        }
        return modelSettingsPayload();
    }

    private String getValue(String key) throws Exception {
        try (Connection conn = store.connect();
             PreparedStatement ps = conn.prepareStatement("SELECT setting_value FROM system_setting WHERE setting_key = ?")) {
            ps.setString(1, key);
            try (ResultSet rs = ps.executeQuery()) {
                return rs.next() ? rs.getString("setting_value") : null;
            }
        }
    }

    private void putValue(String key, String value, long operatorUserId) throws Exception {
        try (Connection conn = store.connect();
             PreparedStatement ps = conn.prepareStatement(
                     "INSERT INTO system_setting(setting_key, setting_value, updated_at, updated_by) VALUES (?, ?, ?, ?) "
                             + "ON CONFLICT(setting_key) DO UPDATE SET setting_value = excluded.setting_value, "
                             + "updated_at = excluded.updated_at, updated_by = excluded.updated_by")) {
            ps.setString(1, key);
            ps.setString(2, value);
            ps.setString(3, SqliteStore.utcNow());
            ps.setString(4, String.valueOf(operatorUserId));
            ps.executeUpdate();
        }
    }

    private String maskKey(String apiKey) {
        if (isBlank(apiKey)) {
            return "";
        }
        return "已配置";
    }

    private boolean isBlank(String value) {
        return value == null || value.trim().isEmpty();
    }
}
