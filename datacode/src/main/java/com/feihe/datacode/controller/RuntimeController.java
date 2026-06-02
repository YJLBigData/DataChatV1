package com.feihe.datacode.controller;

import com.feihe.datacode.config.DataCodeProperties;
import com.feihe.datacode.model.UserInfo;
import com.feihe.datacode.service.AuthService;
import com.feihe.datacode.service.ModelClientService;
import org.springframework.web.bind.annotation.CookieValue;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;

@RestController
@RequestMapping("/api")
public class RuntimeController {
    private final AuthService authService;
    private final ModelClientService modelClientService;
    private final DataCodeProperties properties;

    public RuntimeController(AuthService authService, ModelClientService modelClientService, DataCodeProperties properties) {
        this.authService = authService;
        this.modelClientService = modelClientService;
        this.properties = properties;
    }

    @GetMapping("/health")
    public Map<String, Object> health() {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("ok", true);
        payload.put("service", "datacode-java");
        payload.put("model", properties.getModel().getModelName());
        payload.put("base_url", properties.getModel().getBaseUrl());
        return payload;
    }

    @GetMapping("/runtime/bootstrap")
    public Map<String, Object> bootstrap(@CookieValue(value = AuthService.ACCESS_COOKIE, required = false) String token) {
        UserInfo user = authService.requireUser(token);
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("default_provider_id", properties.getModel().getProviderId());
        payload.put("providers", Collections.singletonList(modelClientService.providerPayload()));
        payload.put("ui_access", authService.uiAccess(user));
        return payload;
    }
}
