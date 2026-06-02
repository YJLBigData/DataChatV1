package com.feihe.datacode.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.feihe.datacode.config.DataCodeProperties;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import okhttp3.ResponseBody;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.time.Duration;
import java.time.Instant;
import java.util.Locale;
import java.util.concurrent.ConcurrentHashMap;

/**
 * 调用 DataChatV1 /api/me 校验 Bearer Token，并把结果按 token 做短期内存缓存，
 * 避免高频转发。命中 401 立刻清理缓存，让前端获得真实的失效响应。
 */
@Component
public class DataChatSsoClient {
    private static final Logger LOG = LoggerFactory.getLogger(DataChatSsoClient.class);

    private final DataCodeProperties properties;
    private final ObjectMapper mapper;
    private final OkHttpClient http;
    private final ConcurrentHashMap<String, CachedUser> cache = new ConcurrentHashMap<>();

    public DataChatSsoClient(DataCodeProperties properties, ObjectMapper mapper) {
        this.properties = properties;
        this.mapper = mapper;
        this.http = new OkHttpClient.Builder()
                .connectTimeout(Duration.ofSeconds(3))
                .readTimeout(Duration.ofSeconds(5))
                .callTimeout(Duration.ofSeconds(8))
                .build();
    }

    public DataChatUser fetchCurrentUser(String bearerToken) throws SsoException {
        if (bearerToken == null || bearerToken.isEmpty()) {
            throw new SsoException(401, "缺少 DataChat token");
        }
        long now = Instant.now().getEpochSecond();
        int ttl = Math.max(1, properties.getSso().getCacheSeconds());
        CachedUser cached = cache.get(bearerToken);
        if (cached != null && cached.expiresAt > now) {
            return cached.user;
        }

        String base = trimTrailingSlash(properties.getSso().getDatachatBaseUrl());
        Request req = new Request.Builder()
                .url(base + "/api/me")
                .header("Authorization", "Bearer " + bearerToken)
                .get()
                .build();
        try (Response resp = http.newCall(req).execute()) {
            if (resp.code() == 401 || resp.code() == 403) {
                cache.remove(bearerToken);
                throw new SsoException(401, "DataChat token 已失效，请重新登录");
            }
            if (!resp.isSuccessful()) {
                LOG.warn("DataChat /api/me 异常: HTTP {}", resp.code());
                throw new SsoException(502, "DataChat 鉴权服务暂不可用");
            }
            ResponseBody body = resp.body();
            if (body == null) {
                throw new SsoException(502, "DataChat /api/me 返回为空");
            }
            JsonNode node = mapper.readTree(body.string());
            DataChatUser user = parse(node);
            cache.put(bearerToken, new CachedUser(user, now + ttl));
            return user;
        } catch (IOException e) {
            LOG.warn("调用 DataChat /api/me 失败: {}", e.getMessage());
            throw new SsoException(502, "DataChat 鉴权服务连接失败");
        }
    }

    public void invalidate(String bearerToken) {
        if (bearerToken != null) {
            cache.remove(bearerToken);
        }
    }

    private DataChatUser parse(JsonNode node) throws SsoException {
        String id = optText(node, "id");
        String username = optText(node, "username");
        String role = optText(node, "role");
        if (id == null || username == null) {
            throw new SsoException(502, "DataChat /api/me 缺少 id/username");
        }
        boolean mustChange = node.path("must_change_password").asBoolean(false);
        String email = optText(node, "email");
        DataChatUser user = new DataChatUser();
        user.id = id;
        user.username = username;
        user.role = (role == null || role.isEmpty()) ? "user" : role.toLowerCase(Locale.ROOT);
        user.email = email == null ? "" : email;
        user.mustChangePassword = mustChange;
        return user;
    }

    private static String optText(JsonNode node, String field) {
        JsonNode value = node == null ? null : node.get(field);
        if (value == null || value.isNull()) return null;
        String text = value.asText();
        return text == null || text.isEmpty() ? null : text;
    }

    private static String trimTrailingSlash(String url) {
        if (url == null || url.isEmpty()) return "";
        return url.endsWith("/") ? url.substring(0, url.length() - 1) : url;
    }

    public static class DataChatUser {
        public String id;
        public String username;
        public String role;
        public String email;
        public boolean mustChangePassword;
    }

    private static class CachedUser {
        final DataChatUser user;
        final long expiresAt;

        CachedUser(DataChatUser user, long expiresAt) {
            this.user = user;
            this.expiresAt = expiresAt;
        }
    }

    public static class SsoException extends RuntimeException {
        private final int status;

        public SsoException(int status, String message) {
            super(message);
            this.status = status;
        }

        public int getStatus() {
            return status;
        }
    }
}
