package com.feihe.datacode.config;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.feihe.datacode.service.AuthService;
import com.feihe.datacode.service.DataChatSsoClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import javax.servlet.FilterChain;
import javax.servlet.ServletException;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * DataChat SSO 入口 Filter。
 * 在 SSO 模式（默认）下：
 *  · 任何带 Authorization: Bearer <token> 的 /api/* 请求，先调用 DataChatV1 /api/me 校验；
 *  · 校验通过 → 在本地建立/更新影子用户 + 关联会话，把 token 透传成 ACCESS_COOKIE，
 *    保持既有 @CookieValue 控制器零改动；
 *  · 校验失败 → 直接 401，不让请求落到业务控制器。
 * 不带 Bearer 的请求保持原状（health/login 等公开接口照走）。
 */
@Component
public class DataChatSsoFilter extends OncePerRequestFilter {
    private static final Logger LOG = LoggerFactory.getLogger(DataChatSsoFilter.class);

    private final DataCodeProperties properties;
    private final DataChatSsoClient ssoClient;
    private final AuthService authService;
    private final ObjectMapper mapper;

    public DataChatSsoFilter(DataCodeProperties properties, DataChatSsoClient ssoClient,
                             AuthService authService, ObjectMapper mapper) {
        this.properties = properties;
        this.ssoClient = ssoClient;
        this.authService = authService;
        this.mapper = mapper;
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        if (!properties.getSso().isEnabled()) {
            return true;
        }
        String path = request.getServletPath();
        if (path == null) {
            return true;
        }
        // 公开端点：健康检查 + 静态资源；不强校 token，避免噪声
        if (path.equals("/api/health")) {
            return true;
        }
        return !path.startsWith("/api/");
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        String bearer = extractBearer(request);
        if (bearer == null || bearer.isEmpty()) {
            // 没有 Bearer：让业务控制器自己 401，不在这里冒充用户
            chain.doFilter(request, response);
            return;
        }
        try {
            DataChatSsoClient.DataChatUser user = ssoClient.fetchCurrentUser(bearer);
            authService.attachSsoSession(bearer, user.username,
                    pickDisplayName(user), user.role);
            chain.doFilter(new SsoCookieRequestWrapper(request, bearer), response);
        } catch (DataChatSsoClient.SsoException e) {
            ssoClient.invalidate(bearer);
            writeJsonError(response, e.getStatus(), e.getMessage());
        } catch (Exception e) {
            LOG.warn("DataChat SSO Filter 异常: {}", e.getMessage());
            writeJsonError(response, 500, "SSO 处理异常: " + e.getMessage());
        }
    }

    private static String extractBearer(HttpServletRequest request) {
        String header = request.getHeader("Authorization");
        if (header == null) {
            return null;
        }
        String trimmed = header.trim();
        if (trimmed.regionMatches(true, 0, "Bearer ", 0, 7)) {
            return trimmed.substring(7).trim();
        }
        return null;
    }

    private static String pickDisplayName(DataChatSsoClient.DataChatUser user) {
        if (user == null) return "";
        if (user.email != null && !user.email.isEmpty()) {
            return user.email;
        }
        return user.username;
    }

    private void writeJsonError(HttpServletResponse response, int status, String message) throws IOException {
        response.setStatus(status);
        response.setContentType("application/json;charset=UTF-8");
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("ok", false);
        body.put("detail", message == null ? "未授权" : message);
        response.getWriter().write(mapper.writeValueAsString(body));
    }
}
