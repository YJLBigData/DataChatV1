package com.feihe.datacode.config;

import com.feihe.datacode.service.AuthService;

import javax.servlet.http.Cookie;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletRequestWrapper;
import java.util.ArrayList;
import java.util.List;

/**
 * SSO 模式下把 Authorization Bearer Token 透传成等值 cookie，
 * 让现有 @CookieValue(ACCESS_COOKIE) 控制器无需改动也能取到当前会话 token。
 */
public class SsoCookieRequestWrapper extends HttpServletRequestWrapper {
    private final Cookie[] effectiveCookies;

    public SsoCookieRequestWrapper(HttpServletRequest request, String bearerToken) {
        super(request);
        Cookie[] original = request.getCookies();
        List<Cookie> merged = new ArrayList<>();
        boolean replaced = false;
        if (original != null) {
            for (Cookie c : original) {
                if (AuthService.ACCESS_COOKIE.equals(c.getName())) {
                    merged.add(buildCookie(bearerToken));
                    replaced = true;
                } else {
                    merged.add(c);
                }
            }
        }
        if (!replaced) {
            merged.add(buildCookie(bearerToken));
        }
        this.effectiveCookies = merged.toArray(new Cookie[0]);
    }

    private static Cookie buildCookie(String value) {
        Cookie cookie = new Cookie(AuthService.ACCESS_COOKIE, value == null ? "" : value);
        cookie.setHttpOnly(true);
        cookie.setPath("/");
        return cookie;
    }

    @Override
    public Cookie[] getCookies() {
        return effectiveCookies;
    }
}
