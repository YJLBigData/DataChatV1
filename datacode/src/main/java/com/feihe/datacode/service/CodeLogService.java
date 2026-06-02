package com.feihe.datacode.service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.feihe.datacode.model.ValidationResult;
import org.springframework.stereotype.Service;

import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@Service
public class CodeLogService {
    private final SqliteStore store;
    private final ObjectMapper mapper;

    public CodeLogService(SqliteStore store, ObjectMapper mapper) {
        this.store = store;
        this.mapper = mapper;
    }

    public void createGeneration(
            String traceId,
            String userId,
            String rawInput,
            String metric,
            String providerId,
            String modelName,
            String promptText,
            String sourceSchema,
            String sourceSamples,
            String notes,
            List<Map<String, Object>> requirements,
            Map<String, Object> traceMeta) throws Exception {
        String now = SqliteStore.utcNow();
        try (Connection conn = store.connect();
             PreparedStatement ps = conn.prepareStatement(
                     "INSERT INTO generation_log(trace_id, user_id, raw_input, metric, status, created_at, updated_at, "
                             + "provider_id, model_name, prompt_text, source_schema, source_samples, notes, requirements_json, trace_meta_json) "
                             + "VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")) {
            ps.setString(1, traceId);
            ps.setString(2, userId);
            ps.setString(3, rawInput);
            ps.setString(4, metric);
            ps.setString(5, now);
            ps.setString(6, now);
            ps.setString(7, providerId);
            ps.setString(8, modelName);
            ps.setString(9, promptText);
            ps.setString(10, sourceSchema);
            ps.setString(11, sourceSamples);
            ps.setString(12, notes);
            ps.setString(13, toJson(requirements));
            ps.setString(14, toJson(traceMeta));
            ps.executeUpdate();
        }
    }

    public void finishGeneration(
            String traceId,
            String status,
            double elapsedMs,
            String generatedSql,
            ValidationResult validation,
            String errorMsg,
            Map<String, Object> traceMeta) throws Exception {
        try (Connection conn = store.connect();
             PreparedStatement ps = conn.prepareStatement(
                     "UPDATE generation_log SET status = ?, elapsed_ms = ?, generated_sql = COALESCE(?, generated_sql), "
                             + "validation_json = COALESCE(?, validation_json), error_msg = ?, trace_meta_json = COALESCE(?, trace_meta_json), "
                             + "updated_at = ? WHERE trace_id = ?")) {
            ps.setString(1, status);
            ps.setDouble(2, elapsedMs);
            ps.setString(3, generatedSql);
            ps.setString(4, validation == null ? null : toJson(validation.toPayload()));
            ps.setString(5, errorMsg);
            ps.setString(6, traceMeta == null ? null : toJson(traceMeta));
            ps.setString(7, SqliteStore.utcNow());
            ps.setString(8, traceId);
            ps.executeUpdate();
        }
    }

    public void logLlmInvocation(
            String traceId,
            String requestId,
            int roundNo,
            String stage,
            String providerId,
            String modelName,
            Map<String, Object> requestPayload,
            Map<String, Object> responsePayload,
            String errorMessage) throws Exception {
        try (Connection conn = store.connect();
             PreparedStatement ps = conn.prepareStatement(
                     "INSERT INTO llm_invocation_log(trace_id, request_id, round_no, stage, provider_id, model_name, "
                             + "request_json, response_json, error_message, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")) {
            ps.setString(1, traceId);
            ps.setString(2, requestId);
            ps.setInt(3, roundNo);
            ps.setString(4, stage);
            ps.setString(5, providerId);
            ps.setString(6, modelName);
            ps.setString(7, toJson(requestPayload));
            ps.setString(8, responsePayload == null ? null : toJson(responsePayload));
            ps.setString(9, errorMessage);
            ps.setString(10, SqliteStore.utcNow());
            ps.executeUpdate();
        }
    }

    public List<Map<String, Object>> listLogs(int limit, int offset, List<String> userIds, String status) throws Exception {
        List<Object> params = new ArrayList<>();
        StringBuilder sql = new StringBuilder(
                "SELECT id, trace_id, user_id, raw_input, metric, status, elapsed_ms, created_at, provider_id, model_name, error_msg "
                        + "FROM generation_log");
        List<String> where = new ArrayList<>();
        if (userIds != null && !userIds.isEmpty()) {
            StringBuilder placeholders = new StringBuilder();
            for (int i = 0; i < userIds.size(); i++) {
                if (i > 0) {
                    placeholders.append(",");
                }
                placeholders.append("?");
                params.add(userIds.get(i));
            }
            where.add("user_id IN (" + placeholders + ")");
        }
        if (status != null && !status.trim().isEmpty()) {
            where.add("status = ?");
            params.add(status.trim());
        }
        if (!where.isEmpty()) {
            sql.append(" WHERE ").append(String.join(" AND ", where));
        }
        sql.append(" ORDER BY created_at DESC LIMIT ? OFFSET ?");
        params.add(limit);
        params.add(offset);
        try (Connection conn = store.connect();
             PreparedStatement ps = conn.prepareStatement(sql.toString())) {
            bind(ps, params);
            try (ResultSet rs = ps.executeQuery()) {
                List<Map<String, Object>> rows = new ArrayList<>();
                while (rs.next()) {
                    Map<String, Object> item = new LinkedHashMap<>();
                    item.put("id", rs.getLong("id"));
                    item.put("trace_id", rs.getString("trace_id"));
                    item.put("user_id", rs.getString("user_id"));
                    item.put("raw_input", rs.getString("raw_input"));
                    item.put("metric", rs.getString("metric"));
                    item.put("status", rs.getString("status"));
                    item.put("elapsed_ms", rs.getObject("elapsed_ms"));
                    item.put("created_at", rs.getString("created_at"));
                    item.put("provider_id", rs.getString("provider_id"));
                    item.put("model_name", rs.getString("model_name"));
                    item.put("error_msg", rs.getString("error_msg"));
                    rows.add(item);
                }
                return rows;
            }
        }
    }

    public int countLogs(List<String> userIds, String status) throws Exception {
        List<Object> params = new ArrayList<>();
        StringBuilder sql = new StringBuilder("SELECT COUNT(*) AS total FROM generation_log");
        List<String> where = new ArrayList<>();
        if (userIds != null && !userIds.isEmpty()) {
            StringBuilder placeholders = new StringBuilder();
            for (int i = 0; i < userIds.size(); i++) {
                if (i > 0) {
                    placeholders.append(",");
                }
                placeholders.append("?");
                params.add(userIds.get(i));
            }
            where.add("user_id IN (" + placeholders + ")");
        }
        if (status != null && !status.trim().isEmpty()) {
            where.add("status = ?");
            params.add(status.trim());
        }
        if (!where.isEmpty()) {
            sql.append(" WHERE ").append(String.join(" AND ", where));
        }
        try (Connection conn = store.connect();
             PreparedStatement ps = conn.prepareStatement(sql.toString())) {
            bind(ps, params);
            try (ResultSet rs = ps.executeQuery()) {
                return rs.next() ? rs.getInt("total") : 0;
            }
        }
    }

    public Map<String, Object> getLog(String traceId) throws Exception {
        try (Connection conn = store.connect();
             PreparedStatement ps = conn.prepareStatement("SELECT * FROM generation_log WHERE trace_id = ?")) {
            ps.setString(1, traceId);
            try (ResultSet rs = ps.executeQuery()) {
                if (!rs.next()) {
                    return null;
                }
                Map<String, Object> item = new LinkedHashMap<>();
                item.put("id", rs.getLong("id"));
                item.put("trace_id", rs.getString("trace_id"));
                item.put("user_id", rs.getString("user_id"));
                item.put("raw_input", rs.getString("raw_input"));
                item.put("metric", rs.getString("metric"));
                item.put("status", rs.getString("status"));
                item.put("elapsed_ms", rs.getObject("elapsed_ms"));
                item.put("created_at", rs.getString("created_at"));
                item.put("updated_at", rs.getString("updated_at"));
                item.put("provider_id", rs.getString("provider_id"));
                item.put("model_name", rs.getString("model_name"));
                item.put("error_msg", rs.getString("error_msg"));
                item.put("prompt_text", rs.getString("prompt_text"));
                item.put("source_schema", rs.getString("source_schema"));
                item.put("source_samples", rs.getString("source_samples"));
                item.put("notes", rs.getString("notes"));
                item.put("requirements", fromJson(rs.getString("requirements_json"), new ArrayList<>()));
                item.put("generated_sql", rs.getString("generated_sql"));
                item.put("validation", fromJson(rs.getString("validation_json"), null));
                item.put("trace_meta", fromJson(rs.getString("trace_meta_json"), new LinkedHashMap<>()));
                return item;
            }
        }
    }

    public List<Map<String, Object>> listInvocations(String traceId) throws Exception {
        try (Connection conn = store.connect();
             PreparedStatement ps = conn.prepareStatement("SELECT * FROM llm_invocation_log WHERE trace_id = ? ORDER BY id")) {
            ps.setString(1, traceId);
            try (ResultSet rs = ps.executeQuery()) {
                List<Map<String, Object>> items = new ArrayList<>();
                while (rs.next()) {
                    Map<String, Object> item = new LinkedHashMap<>();
                    item.put("id", rs.getLong("id"));
                    item.put("trace_id", rs.getString("trace_id"));
                    item.put("request_id", rs.getString("request_id"));
                    item.put("round_no", rs.getInt("round_no"));
                    item.put("stage", rs.getString("stage"));
                    item.put("provider_id", rs.getString("provider_id"));
                    item.put("model_name", rs.getString("model_name"));
                    item.put("request", fromJson(rs.getString("request_json"), new LinkedHashMap<>()));
                    item.put("response", fromJson(rs.getString("response_json"), null));
                    item.put("error_message", rs.getString("error_message"));
                    item.put("created_at", rs.getString("created_at"));
                    items.add(item);
                }
                return items;
            }
        }
    }

    public List<Map<String, String>> listUserOptions() throws Exception {
        try (Connection conn = store.connect();
             PreparedStatement ps = conn.prepareStatement(
                     "SELECT user_id, username, display_name, role FROM users WHERE is_active = 1 ORDER BY user_id");
             ResultSet rs = ps.executeQuery()) {
            List<Map<String, String>> items = new ArrayList<>();
            while (rs.next()) {
                Map<String, String> item = new LinkedHashMap<>();
                item.put("user_id", String.valueOf(rs.getLong("user_id")));
                item.put("username", rs.getString("username"));
                item.put("display_name", rs.getString("display_name"));
                item.put("role", rs.getString("role"));
                item.put("label", rs.getString("display_name") + " (" + rs.getString("username") + ")");
                items.add(item);
            }
            return items;
        }
    }

    private void bind(PreparedStatement ps, List<Object> params) throws Exception {
        for (int i = 0; i < params.size(); i++) {
            ps.setObject(i + 1, params.get(i));
        }
    }

    private String toJson(Object value) throws Exception {
        return mapper.writeValueAsString(value);
    }

    private Object fromJson(String json, Object fallback) {
        if (json == null || json.trim().isEmpty()) {
            return fallback;
        }
        try {
            return mapper.readValue(json, new TypeReference<Object>() {
            });
        } catch (Exception e) {
            return fallback;
        }
    }
}
