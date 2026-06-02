package com.feihe.datacode.config;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.context.annotation.Configuration;

@Configuration
@ConfigurationProperties(prefix = "datacode")
public class DataCodeProperties {
    private String databasePath = "logs/datacode-java.db";
    private String uploadDir = "storage/uploads";
    private Model model = new Model();
    private Maxcompute maxcompute = new Maxcompute();
    private Sso sso = new Sso();

    public String getDatabasePath() {
        return databasePath;
    }

    public void setDatabasePath(String databasePath) {
        this.databasePath = databasePath;
    }

    public String getUploadDir() {
        return uploadDir;
    }

    public void setUploadDir(String uploadDir) {
        this.uploadDir = uploadDir;
    }

    public Model getModel() {
        return model;
    }

    public void setModel(Model model) {
        this.model = model;
    }

    public Maxcompute getMaxcompute() {
        return maxcompute;
    }

    public void setMaxcompute(Maxcompute maxcompute) {
        this.maxcompute = maxcompute;
    }

    public Sso getSso() {
        return sso;
    }

    public void setSso(Sso sso) {
        this.sso = sso;
    }

    public static class Model {
        private String providerId = "qwen-dashscope";
        private String modelName = "qwen3.6-max-preview";
        private String baseUrl = "https://dashscope.aliyuncs.com/compatible-mode/v1";
        private String apiKey = "";
        private double temperature = 0.1D;
        private int maxTokens = 12000;

        public String getProviderId() {
            return providerId;
        }

        public void setProviderId(String providerId) {
            this.providerId = providerId;
        }

        public String getModelName() {
            return modelName;
        }

        public void setModelName(String modelName) {
            this.modelName = modelName;
        }

        public String getBaseUrl() {
            return baseUrl;
        }

        public void setBaseUrl(String baseUrl) {
            this.baseUrl = baseUrl;
        }

        public String getApiKey() {
            return apiKey;
        }

        public void setApiKey(String apiKey) {
            this.apiKey = apiKey;
        }

        public double getTemperature() {
            return temperature;
        }

        public void setTemperature(double temperature) {
            this.temperature = temperature;
        }

        public int getMaxTokens() {
            return maxTokens;
        }

        public void setMaxTokens(int maxTokens) {
            this.maxTokens = maxTokens;
        }
    }

    public static class Maxcompute {
        private String accessKeyId = "";
        private String accessKeySecret = "";
        private String endpoint = "http://service.cn-beijing.maxcompute.aliyun.com/api";
        private String defaultProject = "firmus_dataphin_prd_ads";
        private int readonlyMaxLimit = 1000;

        public String getAccessKeyId() {
            return accessKeyId;
        }

        public void setAccessKeyId(String accessKeyId) {
            this.accessKeyId = accessKeyId;
        }

        public String getAccessKeySecret() {
            return accessKeySecret;
        }

        public void setAccessKeySecret(String accessKeySecret) {
            this.accessKeySecret = accessKeySecret;
        }

        public String getEndpoint() {
            return endpoint;
        }

        public void setEndpoint(String endpoint) {
            this.endpoint = endpoint;
        }

        public String getDefaultProject() {
            return defaultProject;
        }

        public void setDefaultProject(String defaultProject) {
            this.defaultProject = defaultProject;
        }

        public int getReadonlyMaxLimit() {
            return readonlyMaxLimit;
        }

        public void setReadonlyMaxLimit(int readonlyMaxLimit) {
            this.readonlyMaxLimit = readonlyMaxLimit;
        }
    }

    public static class Sso {
        private boolean enabled = true;
        private String datachatBaseUrl = "http://127.0.0.1:8001";
        private int cacheSeconds = 60;
        private String loginRedirect = "/web/#/chat";

        public boolean isEnabled() {
            return enabled;
        }

        public void setEnabled(boolean enabled) {
            this.enabled = enabled;
        }

        public String getDatachatBaseUrl() {
            return datachatBaseUrl;
        }

        public void setDatachatBaseUrl(String datachatBaseUrl) {
            this.datachatBaseUrl = datachatBaseUrl;
        }

        public int getCacheSeconds() {
            return cacheSeconds;
        }

        public void setCacheSeconds(int cacheSeconds) {
            this.cacheSeconds = cacheSeconds;
        }

        public String getLoginRedirect() {
            return loginRedirect;
        }

        public void setLoginRedirect(String loginRedirect) {
            this.loginRedirect = loginRedirect;
        }
    }
}
