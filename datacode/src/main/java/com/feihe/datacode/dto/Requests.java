package com.feihe.datacode.dto;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

public final class Requests {
    private Requests() {
    }

    public static class LoginRequest {
        public String username;
        public String password;
    }

    public static class ChangePasswordRequest {
        public String oldPassword;
        public String newPassword;
    }

    public static class CreateUserRequest {
        public String username;
        public String displayName;
        public String role = "user";
        public String initialPassword;
    }

    public static class UpdateUserRequest {
        public String username;
        public String displayName;
        public String role;
    }

    public static class GenerateCodeRequest {
        public String promptMarkdown = "";
        public String sourceSchema = "";
        public String sourceSamples = "";
        public String notes = "";
        public List<Map<String, Object>> requirements = new ArrayList<>();
        public String requestId;
    }

    public static class ValidateSqlRequest {
        public String sql;
    }

    public static class UpdateModelSettingsRequest {
        public String modelName;
        public String apiKey;
    }

    public static class DataphinQueryRequest {
        public String projectName;
        public String sql;
        public Integer limit = 100;
        public String bizdate;
    }

    public static class DataphinTaskSearchRequest {
        public String projectName;
        public String keyword;
        public String operatorType;
        public String nodeType;
        public Integer limit = 100;
        public String bizdate;
    }

    public static class DataphinTableLineageRequest {
        public String tableName;
        public String projectName;
        public String direction = "both";
        public Integer limit = 100;
        public String bizdate;
    }

    public static class DataphinTaskLineageRequest {
        public String nodeId;
        public String direction = "both";
        public Integer limit = 100;
        public String bizdate;
    }
}
