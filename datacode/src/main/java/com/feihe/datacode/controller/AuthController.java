package com.feihe.datacode.controller;

import com.feihe.datacode.config.DataCodeProperties;
import com.feihe.datacode.dto.Requests.ChangePasswordRequest;
import com.feihe.datacode.dto.Requests.CreateUserRequest;
import com.feihe.datacode.dto.Requests.LoginRequest;
import com.feihe.datacode.dto.Requests.UpdateUserRequest;
import com.feihe.datacode.model.UserInfo;
import com.feihe.datacode.service.AuthService;
import org.springframework.http.HttpHeaders;
import org.springframework.http.ResponseCookie;
import org.springframework.web.bind.annotation.CookieValue;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import javax.servlet.http.HttpServletResponse;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

@RestController
@RequestMapping("/api")
public class AuthController {
    private final AuthService authService;
    private final DataCodeProperties properties;

    public AuthController(AuthService authService, DataCodeProperties properties) {
        this.authService = authService;
        this.properties = properties;
    }

    @PostMapping("/auth/login")
    public Map<String, Object> login(@RequestBody LoginRequest request, HttpServletResponse response) throws Exception {
        if (authService.isSsoEnabled()) {
            // SSO 模式下 DataCode 不再是第二个密码源；引导用户到 DataChat 登录。
            throw new AuthService.AccessDeniedException(400,
                    "DataCode 已接入 DataChat SSO，请先登录 DataChat: " + properties.getSso().getLoginRedirect());
        }
        AuthService.LoginResult result = authService.authenticate(request.username, request.password);
        if (result == null) {
            throw new AuthService.AccessDeniedException(401, "用户名或密码错误");
        }
        ResponseCookie cookie = ResponseCookie.from(AuthService.ACCESS_COOKIE, result.getToken())
                .httpOnly(true)
                .sameSite("Lax")
                .path("/")
                .maxAge(Duration.ofHours(8))
                .build();
        response.addHeader(HttpHeaders.SET_COOKIE, cookie.toString());

        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("ok", true);
        payload.put("user", result.getUser().toPayload());
        payload.put("must_change_password", result.getUser().isMustChangePassword());
        payload.put("ui_access", authService.uiAccess(result.getUser()));
        return payload;
    }

    @PostMapping("/auth/logout")
    public Map<String, Object> logout(@CookieValue(value = AuthService.ACCESS_COOKIE, required = false) String token,
                                      HttpServletResponse response) {
        authService.logout(token);
        ResponseCookie cookie = ResponseCookie.from(AuthService.ACCESS_COOKIE, "")
                .httpOnly(true)
                .sameSite("Lax")
                .path("/")
                .maxAge(Duration.ZERO)
                .build();
        response.addHeader(HttpHeaders.SET_COOKIE, cookie.toString());
        return ok();
    }

    @PostMapping("/auth/refresh")
    public Map<String, Object> refresh(@CookieValue(value = AuthService.ACCESS_COOKIE, required = false) String token) {
        authService.requireUser(token);
        return ok();
    }

    @GetMapping("/auth/me")
    public Map<String, Object> me(@CookieValue(value = AuthService.ACCESS_COOKIE, required = false) String token) {
        UserInfo user = authService.requireUser(token);
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("user", user.toPayload());
        payload.put("permissions", new LinkedHashMap<>());
        payload.put("must_change_password", user.isMustChangePassword());
        payload.put("ui_access", authService.uiAccess(user));
        return payload;
    }

    @PostMapping("/auth/change-password")
    public Map<String, Object> changePassword(@RequestBody ChangePasswordRequest request,
                                              @CookieValue(value = AuthService.ACCESS_COOKIE, required = false) String token) throws Exception {
        requireLocalAuthMode();
        UserInfo user = authService.requireUser(token);
        authService.changePassword(user.getUserId(), request.oldPassword, request.newPassword);
        return ok();
    }

    @GetMapping("/users")
    public Map<String, Object> listUsers(@CookieValue(value = AuthService.ACCESS_COOKIE, required = false) String token) throws Exception {
        authService.requireAdmin(token);
        List<Map<String, Object>> users = authService.listUsers().stream()
                .map(UserInfo::toPayload)
                .collect(Collectors.toList());
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("users", users);
        return payload;
    }

    @PostMapping("/users")
    public Map<String, Object> createUser(@RequestBody CreateUserRequest request,
                                          @CookieValue(value = AuthService.ACCESS_COOKIE, required = false) String token) throws Exception {
        requireLocalAuthMode();
        UserInfo operator = authService.requireAdmin(token);
        UserInfo user = authService.createUser(
                request.username,
                request.displayName,
                request.role,
                request.initialPassword,
                operator.getUserId());
        Map<String, Object> payload = ok();
        payload.put("user", user.toPayload());
        payload.put("initial_password", request.initialPassword == null || request.initialPassword.trim().isEmpty() ? "123456" : request.initialPassword);
        return payload;
    }

    @PutMapping("/users/{userId}")
    public Map<String, Object> updateUser(@PathVariable long userId,
                                          @RequestBody UpdateUserRequest request,
                                          @CookieValue(value = AuthService.ACCESS_COOKIE, required = false) String token) throws Exception {
        requireLocalAuthMode();
        UserInfo operator = authService.requireAdmin(token);
        UserInfo user = authService.updateUser(userId, request.username, request.displayName, request.role, operator.getUserId());
        Map<String, Object> payload = ok();
        payload.put("user", user.toPayload());
        return payload;
    }

    @DeleteMapping("/users/{userId}")
    public Map<String, Object> deleteUser(@PathVariable long userId,
                                          @CookieValue(value = AuthService.ACCESS_COOKIE, required = false) String token) throws Exception {
        requireLocalAuthMode();
        UserInfo operator = authService.requireSuperAdmin(token);
        if (operator.getUserId() == userId) {
            throw new IllegalArgumentException("不能禁用自己的账号");
        }
        authService.deactivateUser(userId, operator.getUserId());
        return ok();
    }

    @PostMapping("/users/{userId}/reset-password")
    public Map<String, Object> resetPassword(@PathVariable long userId,
                                             @CookieValue(value = AuthService.ACCESS_COOKIE, required = false) String token) throws Exception {
        requireLocalAuthMode();
        UserInfo operator = authService.requireAdmin(token);
        String password = authService.resetPassword(userId, operator.getUserId());
        Map<String, Object> payload = ok();
        payload.put("new_password", password);
        return payload;
    }

    private void requireLocalAuthMode() {
        if (authService.isSsoEnabled()) {
            throw new AuthService.AccessDeniedException(403,
                    "DataCode 已接入 DataChat SSO，账号/密码管理请在 DataChat 完成");
        }
    }

    private static Map<String, Object> ok() {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("ok", true);
        return payload;
    }
}
