package com.feihe.datacode.controller;

import com.feihe.datacode.dto.Requests.DataphinQueryRequest;
import com.feihe.datacode.dto.Requests.DataphinTableLineageRequest;
import com.feihe.datacode.dto.Requests.DataphinTaskLineageRequest;
import com.feihe.datacode.dto.Requests.DataphinTaskSearchRequest;
import com.feihe.datacode.service.AuthService;
import com.feihe.datacode.service.DataphinService;
import org.springframework.web.bind.annotation.CookieValue;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

@RestController
@RequestMapping("/api/dataphin")
public class DataphinController {
    private final AuthService authService;
    private final DataphinService dataphinService;

    public DataphinController(AuthService authService, DataphinService dataphinService) {
        this.authService = authService;
        this.dataphinService = dataphinService;
    }

    @GetMapping("/config")
    public Map<String, Object> config(@CookieValue(value = AuthService.ACCESS_COOKIE, required = false) String token) {
        authService.requireAdmin(token);
        return dataphinService.config();
    }

    @PostMapping("/query")
    public Map<String, Object> query(@RequestBody DataphinQueryRequest request,
                                     @CookieValue(value = AuthService.ACCESS_COOKIE, required = false) String token) throws Exception {
        authService.requireAdmin(token);
        return dataphinService.executeDataphinQuery(
                request.sql,
                request.projectName,
                request.limit,
                request.bizdate,
                true).toPayload();
    }

    @PostMapping("/tasks")
    public Map<String, Object> tasks(@RequestBody DataphinTaskSearchRequest request,
                                     @CookieValue(value = AuthService.ACCESS_COOKIE, required = false) String token) throws Exception {
        authService.requireAdmin(token);
        return dataphinService.queryTaskNodes(
                request.projectName,
                request.keyword,
                request.operatorType,
                request.nodeType,
                request.limit,
                request.bizdate).toPayload();
    }

    @PostMapping("/table-lineage")
    public Map<String, Object> tableLineage(@RequestBody DataphinTableLineageRequest request,
                                            @CookieValue(value = AuthService.ACCESS_COOKIE, required = false) String token) throws Exception {
        authService.requireAdmin(token);
        return dataphinService.queryTableLineage(
                request.tableName,
                request.projectName,
                request.direction,
                request.limit,
                request.bizdate).toPayload();
    }

    @PostMapping("/task-lineage")
    public Map<String, Object> taskLineage(@RequestBody DataphinTaskLineageRequest request,
                                           @CookieValue(value = AuthService.ACCESS_COOKIE, required = false) String token) throws Exception {
        authService.requireAdmin(token);
        return dataphinService.queryTaskLineage(
                request.nodeId,
                request.direction,
                request.limit,
                request.bizdate).toPayload();
    }
}
