package com.feihe.datacode.controller;

import com.feihe.datacode.dto.Requests.UpdateModelSettingsRequest;
import com.feihe.datacode.model.UserInfo;
import com.feihe.datacode.service.AuthService;
import com.feihe.datacode.service.SettingsService;
import org.springframework.web.bind.annotation.CookieValue;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

@RestController
@RequestMapping("/api/settings")
public class SettingsController {
    private final AuthService authService;
    private final SettingsService settingsService;

    public SettingsController(AuthService authService, SettingsService settingsService) {
        this.authService = authService;
        this.settingsService = settingsService;
    }

    @GetMapping("/model")
    public Map<String, Object> modelSettings(@CookieValue(value = AuthService.ACCESS_COOKIE, required = false) String token) {
        authService.requireAdmin(token);
        return settingsService.modelSettingsPayload();
    }

    @PostMapping("/model")
    public Map<String, Object> updateModelSettings(@RequestBody UpdateModelSettingsRequest request,
                                                   @CookieValue(value = AuthService.ACCESS_COOKIE, required = false) String token) throws Exception {
        UserInfo user = authService.requireAdmin(token);
        return settingsService.updateModelSettings(request.modelName, request.apiKey, user.getUserId());
    }
}
