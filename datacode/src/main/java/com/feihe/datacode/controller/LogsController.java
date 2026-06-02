package com.feihe.datacode.controller;

import com.feihe.datacode.model.UserInfo;
import com.feihe.datacode.service.AuthService;
import com.feihe.datacode.service.CodeLogService;
import org.springframework.web.bind.annotation.CookieValue;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/logs")
public class LogsController {
    private final AuthService authService;
    private final CodeLogService codeLogService;

    public LogsController(AuthService authService, CodeLogService codeLogService) {
        this.authService = authService;
        this.codeLogService = codeLogService;
    }

    @GetMapping
    public Map<String, Object> listLogs(@RequestParam(defaultValue = "50") int limit,
                                        @RequestParam(defaultValue = "0") int offset,
                                        @RequestParam(required = false) String status,
                                        @RequestParam(required = false) String userId,
                                        @CookieValue(value = AuthService.ACCESS_COOKIE, required = false) String token) throws Exception {
        UserInfo user = authService.requireUser(token);
        List<String> userScope = resolveUserScope(user, userId);
        int safeLimit = Math.max(1, Math.min(limit, 200));
        int safeOffset = Math.max(0, offset);
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("items", codeLogService.listLogs(safeLimit, safeOffset, userScope, status));
        payload.put("total", codeLogService.countLogs(userScope, status));
        return payload;
    }

    @GetMapping("/{traceId}")
    public Map<String, Object> getLog(@PathVariable String traceId,
                                      @CookieValue(value = AuthService.ACCESS_COOKIE, required = false) String token) throws Exception {
        UserInfo user = authService.requireUser(token);
        Map<String, Object> item = codeLogService.getLog(traceId);
        if (item == null) {
            throw new IllegalArgumentException("日志不存在");
        }
        if (!isAdmin(user) && !String.valueOf(user.getUserId()).equals(String.valueOf(item.get("user_id")))) {
            throw new AuthService.AccessDeniedException(403, "不能查看其他用户日志");
        }
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("item", item);
        return payload;
    }

    @GetMapping("/{traceId}/llm")
    public Map<String, Object> getInvocations(@PathVariable String traceId,
                                              @CookieValue(value = AuthService.ACCESS_COOKIE, required = false) String token) throws Exception {
        UserInfo user = authService.requireUser(token);
        Map<String, Object> item = codeLogService.getLog(traceId);
        if (item == null) {
            throw new IllegalArgumentException("日志不存在");
        }
        if (!isAdmin(user) && !String.valueOf(user.getUserId()).equals(String.valueOf(item.get("user_id")))) {
            throw new AuthService.AccessDeniedException(403, "不能查看其他用户日志");
        }
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("items", codeLogService.listInvocations(traceId));
        return payload;
    }

    @GetMapping("/users")
    public Map<String, Object> userOptions(@CookieValue(value = AuthService.ACCESS_COOKIE, required = false) String token) throws Exception {
        authService.requireAdmin(token);
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("users", codeLogService.listUserOptions());
        return payload;
    }

    private List<String> resolveUserScope(UserInfo currentUser, String requestedUserId) {
        if (isAdmin(currentUser)) {
            return requestedUserId == null || requestedUserId.trim().isEmpty()
                    ? null
                    : Collections.singletonList(requestedUserId.trim());
        }
        return Collections.singletonList(String.valueOf(currentUser.getUserId()));
    }

    private boolean isAdmin(UserInfo user) {
        String role = AuthService.normalizeRole(user.getRole());
        return "admin".equals(role) || "super_admin".equals(role);
    }
}
