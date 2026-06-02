package com.feihe.datacode.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.feihe.datacode.config.DataCodeProperties;
import com.feihe.datacode.model.UserInfo;
import org.mindrot.jbcrypt.BCrypt;
import org.springframework.stereotype.Service;

import javax.annotation.PostConstruct;
import java.security.SecureRandom;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.Statement;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;
import java.util.regex.Pattern;

@Service
public class AuthService {
    public static final String ACCESS_COOKIE = "datacode_java_access_token";
    private static final long SESSION_TTL_SECONDS = 8 * 60 * 60L;
    // SSO 会话 TTL：与缓存校验间隔同量级；过期后下次请求 Filter 会再次校验 DataChat token。
    private static final long SSO_SESSION_TTL_SECONDS = 5 * 60L;
    private static final Pattern USERNAME_PATTERN = Pattern.compile("^[a-zA-Z0-9_@.\\-\\u4e00-\\u9fa5]{2,64}$");

    private final SqliteStore store;
    private final ObjectMapper mapper;
    private final DataCodeProperties properties;
    private final SecureRandom random = new SecureRandom();
    private final Map<String, SessionInfo> sessions = new ConcurrentHashMap<>();

    public AuthService(SqliteStore store, ObjectMapper mapper, DataCodeProperties properties) {
        this.store = store;
        this.mapper = mapper;
        this.properties = properties;
    }

    public boolean isSsoEnabled() {
        return properties != null && properties.getSso() != null && properties.getSso().isEnabled();
    }

    @PostConstruct
    public void ensureDefaultAdmin() throws Exception {
        // SSO 模式下不再自带本地 admin 账号；登录由 DataChatV1 统一签发 token。
        if (isSsoEnabled()) {
            return;
        }
        try (Connection conn = store.connect();
             Statement statement = conn.createStatement();
             ResultSet rs = statement.executeQuery("SELECT user_id FROM users WHERE is_active = 1 LIMIT 1")) {
            if (rs.next()) {
                return;
            }
        }
        String now = SqliteStore.utcNow();
        try (Connection conn = store.connect();
             PreparedStatement ps = conn.prepareStatement(
                     "INSERT INTO users(username, display_name, password_hash, role, must_change_password, is_active, created_at) "
                             + "VALUES (?, ?, ?, ?, ?, ?, ?)")) {
            ps.setString(1, "admin");
            ps.setString(2, "系统管理员");
            ps.setString(3, hashPassword("123456"));
            ps.setString(4, "super_admin");
            ps.setInt(5, 1);
            ps.setInt(6, 1);
            ps.setString(7, now);
            ps.executeUpdate();
        }
    }

    public LoginResult authenticate(String username, String password) throws Exception {
        UserInfo user = getUserByUsername(username);
        if (user == null) {
            return null;
        }
        String hash;
        try (Connection conn = store.connect();
             PreparedStatement ps = conn.prepareStatement("SELECT password_hash FROM users WHERE user_id = ? AND is_active = 1")) {
            ps.setLong(1, user.getUserId());
            try (ResultSet rs = ps.executeQuery()) {
                if (!rs.next()) {
                    return null;
                }
                hash = rs.getString("password_hash");
            }
        }
        if (!BCrypt.checkpw(nullToEmpty(password), hash)) {
            return null;
        }
        try (Connection conn = store.connect();
             PreparedStatement ps = conn.prepareStatement("UPDATE users SET last_login = ? WHERE user_id = ?")) {
            ps.setString(1, SqliteStore.utcNow());
            ps.setLong(2, user.getUserId());
            ps.executeUpdate();
        }
        user = getUserById(user.getUserId());
        String token = issueSession(user.getUserId());
        return new LoginResult(token, user);
    }

    public void logout(String token) {
        if (token != null) {
            sessions.remove(token);
        }
    }

    /**
     * 为 DataChat SSO 用户挂载/刷新本地影子记录与本地会话。
     * 调用方（SSO Filter）已经用 DataChat 的 /api/me 校验过 token；这里只负责：
     *  · 在本地 users 表里维护一条影子记录（用于审计/日志归属/角色）
     *  · 把 Bearer Token 关联到该 user_id，沿用既有 @CookieValue 鉴权链路
     * SSO 用户绝不能在本地登录（password_hash 为不可用 placeholder）。
     */
    public UserInfo attachSsoSession(String bearerToken, String externalUsername,
                                     String externalDisplayName, String externalRole) throws Exception {
        if (bearerToken == null || bearerToken.isEmpty()) {
            throw new IllegalArgumentException("SSO bearer token 不能为空");
        }
        UserInfo shadow = ensureSsoShadowUser(externalUsername, externalDisplayName, externalRole);
        sessions.put(bearerToken,
                new SessionInfo(shadow.getUserId(), Instant.now().getEpochSecond() + SSO_SESSION_TTL_SECONDS));
        return shadow;
    }

    private UserInfo ensureSsoShadowUser(String externalUsername, String externalDisplayName,
                                         String externalRole) throws Exception {
        String username = nullToEmpty(externalUsername).trim().toLowerCase(Locale.ROOT);
        if (username.isEmpty()) {
            throw new IllegalArgumentException("DataChat 用户名为空，无法建立 SSO 影子用户");
        }
        String displayName = nullToEmpty(externalDisplayName).trim();
        if (displayName.isEmpty()) {
            displayName = username;
        }
        String role = mapExternalRole(externalRole);
        UserInfo existing = getUserByUsername(username);
        if (existing == null) {
            // SSO 影子账号：password_hash 用占位字符串，确保本地 authenticate() 永远失败
            String now = SqliteStore.utcNow();
            try (Connection conn = store.connect();
                 PreparedStatement ps = conn.prepareStatement(
                         "INSERT INTO users(username, display_name, password_hash, role, must_change_password, is_active, created_at) "
                                 + "VALUES (?, ?, ?, ?, 0, 1, ?)",
                         Statement.RETURN_GENERATED_KEYS)) {
                ps.setString(1, username);
                ps.setString(2, displayName);
                ps.setString(3, "!sso-no-local-login!");
                ps.setString(4, role);
                ps.setString(5, now);
                ps.executeUpdate();
            }
            existing = getUserByUsername(username);
            if (existing == null) {
                throw new IllegalStateException("创建 SSO 影子用户失败: " + username);
            }
            logAudit(existing.getUserId(), "sso_create_user", existing.getUserId(),
                    mapOf("username", username, "role", role), null, null);
        } else {
            boolean needRoleUpdate = !role.equals(normalizeRole(existing.getRole()));
            boolean needNameUpdate = !displayName.equals(nullToEmpty(existing.getDisplayName()));
            if (needRoleUpdate || needNameUpdate) {
                try (Connection conn = store.connect();
                     PreparedStatement ps = conn.prepareStatement(
                             "UPDATE users SET display_name = ?, role = ?, last_login = ? WHERE user_id = ?")) {
                    ps.setString(1, displayName);
                    ps.setString(2, role);
                    ps.setString(3, SqliteStore.utcNow());
                    ps.setLong(4, existing.getUserId());
                    ps.executeUpdate();
                }
                existing = getUserById(existing.getUserId());
            } else {
                try (Connection conn = store.connect();
                     PreparedStatement ps = conn.prepareStatement(
                             "UPDATE users SET last_login = ? WHERE user_id = ?")) {
                    ps.setString(1, SqliteStore.utcNow());
                    ps.setLong(2, existing.getUserId());
                    ps.executeUpdate();
                }
            }
        }
        return existing;
    }

    private static String mapExternalRole(String externalRole) {
        String text = nullToEmpty(externalRole).trim().toLowerCase(Locale.ROOT);
        if ("admin".equals(text) || "super_admin".equals(text)) {
            return "admin";
        }
        return "user";
    }

    public UserInfo requireUser(String token) {
        return getCurrentUser(token).orElseThrow(() -> new AccessDeniedException(401, "未登录或登录已过期"));
    }

    public UserInfo requireAdmin(String token) {
        UserInfo user = requireUser(token);
        String role = normalizeRole(user.getRole());
        if (!"admin".equals(role) && !"super_admin".equals(role)) {
            throw new AccessDeniedException(403, "需要管理员权限");
        }
        return user;
    }

    public UserInfo requireSuperAdmin(String token) {
        UserInfo user = requireUser(token);
        if (!"super_admin".equals(normalizeRole(user.getRole()))) {
            throw new AccessDeniedException(403, "需要超级管理员权限");
        }
        return user;
    }

    public Optional<UserInfo> getCurrentUser(String token) {
        if (token == null || token.trim().isEmpty()) {
            return Optional.empty();
        }
        SessionInfo session = sessions.get(token);
        if (session == null || session.expiresAtEpochSecond < Instant.now().getEpochSecond()) {
            sessions.remove(token);
            return Optional.empty();
        }
        try {
            return Optional.ofNullable(getUserById(session.userId));
        } catch (Exception e) {
            sessions.remove(token);
            return Optional.empty();
        }
    }

    public List<UserInfo> listUsers() throws Exception {
        List<UserInfo> users = new ArrayList<>();
        try (Connection conn = store.connect();
             PreparedStatement ps = conn.prepareStatement("SELECT * FROM users WHERE is_active = 1 ORDER BY user_id");
             ResultSet rs = ps.executeQuery()) {
            while (rs.next()) {
                users.add(readUser(rs));
            }
        }
        return users;
    }

    public UserInfo createUser(String username, String displayName, String role, String initialPassword, long operatorUserId) throws Exception {
        String normalizedRole = normalizeRole(role);
        String cleanUsername = nullToEmpty(username).trim();
        String cleanDisplayName = nullToEmpty(displayName).trim();
        if (!USERNAME_PATTERN.matcher(cleanUsername).matches()) {
            throw new IllegalArgumentException("用户名格式不合法（2-64位，支持字母/数字/下划线/邮件地址）");
        }
        if (cleanDisplayName.isEmpty()) {
            throw new IllegalArgumentException("显示名称不能为空");
        }
        if (!normalizedRole.matches("super_admin|admin|user")) {
            throw new IllegalArgumentException("无效角色: " + role);
        }
        String password = nullToEmpty(initialPassword).trim().isEmpty() ? "123456" : initialPassword.trim();
        long userId;
        try (Connection conn = store.connect();
             PreparedStatement ps = conn.prepareStatement(
                     "INSERT INTO users(username, display_name, password_hash, role, must_change_password, is_active, created_at) "
                             + "VALUES (?, ?, ?, ?, 1, 1, ?)",
                     Statement.RETURN_GENERATED_KEYS)) {
            ps.setString(1, cleanUsername);
            ps.setString(2, cleanDisplayName);
            ps.setString(3, hashPassword(password));
            ps.setString(4, normalizedRole);
            ps.setString(5, SqliteStore.utcNow());
            ps.executeUpdate();
            try (ResultSet rs = ps.getGeneratedKeys()) {
                userId = rs.next() ? rs.getLong(1) : 0L;
            }
        } catch (Exception e) {
            throw new IllegalArgumentException("用户名 '" + cleanUsername + "' 已存在", e);
        }
        logAudit(operatorUserId, "create_user", userId, mapOf("username", cleanUsername, "role", normalizedRole), null, null);
        return getUserById(userId);
    }

    public UserInfo updateUser(long userId, String username, String displayName, String role, long operatorUserId) throws Exception {
        UserInfo existing = getUserById(userId);
        if (existing == null) {
            throw new IllegalArgumentException("用户不存在");
        }
        String newUsername = username == null ? existing.getUsername() : username.trim();
        String newDisplayName = displayName == null ? existing.getDisplayName() : displayName.trim();
        String newRole = role == null ? existing.getRole() : normalizeRole(role);
        if (newUsername.isEmpty() || !USERNAME_PATTERN.matcher(newUsername).matches()) {
            throw new IllegalArgumentException("用户名格式不合法");
        }
        if (newDisplayName.isEmpty()) {
            throw new IllegalArgumentException("显示名称不能为空");
        }
        if (!newRole.matches("super_admin|admin|user")) {
            throw new IllegalArgumentException("无效角色: " + role);
        }
        try (Connection conn = store.connect();
             PreparedStatement ps = conn.prepareStatement(
                     "UPDATE users SET username = ?, display_name = ?, role = ? WHERE user_id = ? AND is_active = 1")) {
            ps.setString(1, newUsername);
            ps.setString(2, newDisplayName);
            ps.setString(3, newRole);
            ps.setLong(4, userId);
            ps.executeUpdate();
        }
        logAudit(operatorUserId, "update_user", userId, mapOf("username", newUsername, "role", newRole), null, null);
        return getUserById(userId);
    }

    public void deactivateUser(long userId, long operatorUserId) throws Exception {
        try (Connection conn = store.connect();
             PreparedStatement ps = conn.prepareStatement("UPDATE users SET is_active = 0 WHERE user_id = ?")) {
            ps.setLong(1, userId);
            ps.executeUpdate();
        }
        sessions.entrySet().removeIf(entry -> entry.getValue().userId == userId);
        logAudit(operatorUserId, "deactivate_user", userId, new LinkedHashMap<>(), null, null);
    }

    public String resetPassword(long userId, long operatorUserId) throws Exception {
        if (getUserById(userId) == null) {
            throw new IllegalArgumentException("用户不存在");
        }
        String password = "123456";
        try (Connection conn = store.connect();
             PreparedStatement ps = conn.prepareStatement("UPDATE users SET password_hash = ?, must_change_password = 1 WHERE user_id = ?")) {
            ps.setString(1, hashPassword(password));
            ps.setLong(2, userId);
            ps.executeUpdate();
        }
        sessions.entrySet().removeIf(entry -> entry.getValue().userId == userId);
        logAudit(operatorUserId, "reset_password", userId, new LinkedHashMap<>(), null, null);
        return password;
    }

    public void changePassword(long userId, String oldPassword, String newPassword) throws Exception {
        if (nullToEmpty(newPassword).length() < 6) {
            throw new IllegalArgumentException("新密码长度至少 6 位");
        }
        String hash;
        try (Connection conn = store.connect();
             PreparedStatement ps = conn.prepareStatement("SELECT password_hash FROM users WHERE user_id = ? AND is_active = 1")) {
            ps.setLong(1, userId);
            try (ResultSet rs = ps.executeQuery()) {
                if (!rs.next()) {
                    throw new IllegalArgumentException("用户不存在");
                }
                hash = rs.getString("password_hash");
            }
        }
        if (!BCrypt.checkpw(nullToEmpty(oldPassword), hash)) {
            throw new IllegalArgumentException("当前密码错误");
        }
        try (Connection conn = store.connect();
             PreparedStatement ps = conn.prepareStatement("UPDATE users SET password_hash = ?, must_change_password = 0 WHERE user_id = ?")) {
            ps.setString(1, hashPassword(newPassword));
            ps.setLong(2, userId);
            ps.executeUpdate();
        }
        sessions.entrySet().removeIf(entry -> entry.getValue().userId == userId);
    }

    public Map<String, Object> uiAccess(UserInfo user) {
        boolean isAdmin = "admin".equals(normalizeRole(user.getRole())) || "super_admin".equals(normalizeRole(user.getRole()));
        boolean isSuperAdmin = "super_admin".equals(normalizeRole(user.getRole()));
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("show_code_tab", true);
        payload.put("show_logs_tab", true);
        payload.put("show_dataphin_tab", isAdmin);
        payload.put("show_users_tab", isAdmin);
        payload.put("show_settings", isAdmin);
        payload.put("can_query_logs", true);
        payload.put("log_scope", isAdmin ? "all" : "self");
        payload.put("can_manage_dataphin", isAdmin);
        payload.put("can_manage_users", isAdmin);
        payload.put("can_delete_user", isSuperAdmin);
        return payload;
    }

    public void logAudit(long operatorUserId, String action, Long targetUserId, Map<String, Object> detail, String traceId, String requestId) {
        try (Connection conn = store.connect();
             PreparedStatement ps = conn.prepareStatement(
                     "INSERT INTO admin_audit_log(operator_user_id, action, target_user_id, detail, trace_id, request_id, created_at) "
                             + "VALUES (?, ?, ?, ?, ?, ?, ?)")) {
            ps.setLong(1, operatorUserId);
            ps.setString(2, action);
            if (targetUserId == null) {
                ps.setObject(3, null);
            } else {
                ps.setLong(3, targetUserId);
            }
            ps.setString(4, toJson(detail));
            ps.setString(5, traceId);
            ps.setString(6, requestId);
            ps.setString(7, SqliteStore.utcNow());
            ps.executeUpdate();
        } catch (Exception ignored) {
            // 审计失败不影响主流程。
        }
    }

    public static String normalizeRole(String role) {
        String text = nullToEmpty(role).trim();
        Map<String, String> aliases = new LinkedHashMap<>();
        aliases.put("超级管理员", "super_admin");
        aliases.put("超管", "super_admin");
        aliases.put("superadmin", "super_admin");
        aliases.put("super-admin", "super_admin");
        aliases.put("管理员", "admin");
        aliases.put("普通管理员", "admin");
        aliases.put("普通用户", "user");
        aliases.put("用户", "user");
        return aliases.getOrDefault(text, text.isEmpty() ? "user" : text.toLowerCase(Locale.ROOT));
    }

    private UserInfo getUserByUsername(String username) throws Exception {
        try (Connection conn = store.connect();
             PreparedStatement ps = conn.prepareStatement("SELECT * FROM users WHERE username = ? AND is_active = 1")) {
            ps.setString(1, nullToEmpty(username).trim());
            try (ResultSet rs = ps.executeQuery()) {
                return rs.next() ? readUser(rs) : null;
            }
        }
    }

    public UserInfo getUserById(long userId) throws Exception {
        try (Connection conn = store.connect();
             PreparedStatement ps = conn.prepareStatement("SELECT * FROM users WHERE user_id = ? AND is_active = 1")) {
            ps.setLong(1, userId);
            try (ResultSet rs = ps.executeQuery()) {
                return rs.next() ? readUser(rs) : null;
            }
        }
    }

    private UserInfo readUser(ResultSet rs) throws Exception {
        UserInfo user = new UserInfo();
        user.setUserId(rs.getLong("user_id"));
        user.setUsername(rs.getString("username"));
        user.setDisplayName(rs.getString("display_name"));
        user.setRole(rs.getString("role"));
        user.setMustChangePassword(rs.getInt("must_change_password") == 1);
        user.setActive(rs.getInt("is_active") == 1);
        user.setCreatedAt(rs.getString("created_at"));
        user.setLastLogin(rs.getString("last_login"));
        return user;
    }

    private String issueSession(long userId) {
        byte[] tokenBytes = new byte[48];
        random.nextBytes(tokenBytes);
        String token = Base64.getUrlEncoder().withoutPadding().encodeToString(tokenBytes);
        sessions.put(token, new SessionInfo(userId, Instant.now().getEpochSecond() + SESSION_TTL_SECONDS));
        return token;
    }

    private String hashPassword(String password) {
        return BCrypt.hashpw(password, BCrypt.gensalt(12));
    }

    private String toJson(Object value) {
        try {
            return mapper.writeValueAsString(value);
        } catch (JsonProcessingException e) {
            return "{}";
        }
    }

    private static String nullToEmpty(String value) {
        return value == null ? "" : value;
    }

    private static Map<String, Object> mapOf(String key1, Object value1, String key2, Object value2) {
        Map<String, Object> map = new LinkedHashMap<>();
        map.put(key1, value1);
        map.put(key2, value2);
        return map;
    }

    public static class LoginResult {
        private final String token;
        private final UserInfo user;

        LoginResult(String token, UserInfo user) {
            this.token = token;
            this.user = user;
        }

        public String getToken() {
            return token;
        }

        public UserInfo getUser() {
            return user;
        }
    }

    private static class SessionInfo {
        private final long userId;
        private final long expiresAtEpochSecond;

        SessionInfo(long userId, long expiresAtEpochSecond) {
            this.userId = userId;
            this.expiresAtEpochSecond = expiresAtEpochSecond;
        }
    }

    public static class AccessDeniedException extends RuntimeException {
        private final int status;

        public AccessDeniedException(int status, String message) {
            super(message);
            this.status = status;
        }

        public int getStatus() {
            return status;
        }
    }
}
