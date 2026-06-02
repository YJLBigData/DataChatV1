package com.feihe.datacode.service;

import com.feihe.datacode.config.DataCodeProperties;
import com.feihe.datacode.dto.Requests.GenerateCodeRequest;
import com.feihe.datacode.model.UserInfo;
import com.feihe.datacode.model.ValidationResult;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.stream.Collectors;

@Service
public class CodeGenerationService {
    private final ModelClientService modelClient;
    private final SqlValidatorService sqlValidator;
    private final DataphinService dataphinService;
    private final CodeLogService codeLogService;
    private final DataCodeProperties properties;

    public CodeGenerationService(
            ModelClientService modelClient,
            SqlValidatorService sqlValidator,
            DataphinService dataphinService,
            CodeLogService codeLogService,
            DataCodeProperties properties) {
        this.modelClient = modelClient;
        this.sqlValidator = sqlValidator;
        this.dataphinService = dataphinService;
        this.codeLogService = codeLogService;
        this.properties = properties;
    }

    public Map<String, Object> generate(GenerateCodeRequest request, UserInfo user) throws Exception {
        if (request.requirements == null || request.requirements.isEmpty()) {
            throw new IllegalArgumentException("请先上传并选择至少一个需求 Sheet");
        }
        String traceId = UUID.randomUUID().toString().replace("-", "");
        String requestId = isBlank(request.requestId) ? UUID.randomUUID().toString().replace("-", "") : request.requestId;
        long startNs = System.nanoTime();
        String rawInput = rawInputSummary(request);
        String metric = metricName(request.requirements);
        DataphinService.SourceMetadata sourceMetadata = dataphinService.collectRequiredSourceMetadata(request.requirements, request.notes);
        String sourceSchema = sourceMetadata.getSourceSchema();
        String sourceSamples = sourceMetadata.getSourceSamples();
        String dataphinContext = dataphinService.collectGenerationDataphinContext(
                request.requirements,
                sourceSchema,
                sourceSamples,
                request.notes);
        List<Map<String, String>> messages = modelClient.buildGenerationMessages(
                request.promptMarkdown,
                request.requirements,
                sourceSchema,
                sourceSamples,
                request.notes,
                dataphinContext);
        Map<String, Object> traceMeta = new LinkedHashMap<>();
        traceMeta.put("request_id", requestId);
        traceMeta.put("stage", "submitted");
        codeLogService.createGeneration(
                traceId,
                String.valueOf(user.getUserId()),
                rawInput,
                metric,
                properties.getModel().getProviderId(),
                properties.getModel().getModelName(),
                request.promptMarkdown,
                sourceSchema,
                sourceSamples,
                request.notes,
                request.requirements,
                traceMeta);
        try {
            ModelClientService.CompletionResult completion = modelClient.complete(messages);
            codeLogService.logLlmInvocation(
                    traceId,
                    requestId,
                    1,
                    "generate_sql",
                    properties.getModel().getProviderId(),
                    properties.getModel().getModelName(),
                    completion.getRequestPayload(),
                    completion.getResponsePayload(),
                    null);
            String sql = sqlValidator.extractSql(completion.getContent());
            ValidationResult validation = sqlValidator.validateDataphinSql(sql);
            boolean repaired = false;
            if (!validation.isValid()) {
                List<Map<String, String>> repairMessages = modelClient.buildRepairMessages(sql, validation.getErrors(), messages);
                ModelClientService.CompletionResult repair = modelClient.complete(repairMessages);
                codeLogService.logLlmInvocation(
                        traceId,
                        requestId,
                        2,
                        "repair_sql",
                        properties.getModel().getProviderId(),
                        properties.getModel().getModelName(),
                        repair.getRequestPayload(),
                        repair.getResponsePayload(),
                        null);
                String repairedSql = sqlValidator.extractSql(repair.getContent());
                ValidationResult repairedValidation = sqlValidator.validateDataphinSql(repairedSql);
                if (repairedValidation.isValid() || repairedValidation.getErrors().size() <= validation.getErrors().size()) {
                    sql = repairedSql;
                    validation = repairedValidation;
                    repaired = true;
                }
            }
            double elapsedMs = elapsedMs(startNs);
            String status = validation.isValid() ? "success" : "validation_failed";
            Map<String, Object> finishedMeta = new LinkedHashMap<>();
            finishedMeta.put("request_id", requestId);
            finishedMeta.put("repaired", repaired);
            finishedMeta.put("stage", "finished");
            codeLogService.finishGeneration(
                    traceId,
                    status,
                    elapsedMs,
                    sql,
                    validation,
                    validation.isValid() ? null : String.join("; ", validation.getErrors()),
                    finishedMeta);

            Map<String, Object> payload = new LinkedHashMap<>();
            payload.put("trace_id", traceId);
            payload.put("request_id", requestId);
            payload.put("status", status);
            payload.put("sql", sql);
            payload.put("validation", validation.toPayload());
            payload.put("repaired", repaired);
            payload.put("provider", provider());
            payload.put("elapsed_ms", elapsedMs);
            return payload;
        } catch (Exception e) {
            double elapsedMs = elapsedMs(startNs);
            codeLogService.logLlmInvocation(
                    traceId,
                    requestId,
                    1,
                    "generate_sql",
                    properties.getModel().getProviderId(),
                    properties.getModel().getModelName(),
                    mapOf("messages", messages),
                    null,
                    e.getMessage());
            Map<String, Object> failedMeta = new LinkedHashMap<>();
            failedMeta.put("request_id", requestId);
            failedMeta.put("stage", "failed");
            codeLogService.finishGeneration(traceId, "failed", elapsedMs, null, null, e.getMessage(), failedMeta);
            throw e;
        }
    }

    private String rawInputSummary(GenerateCodeRequest request) {
        List<String> parts = new ArrayList<>();
        for (Map<String, Object> item : request.requirements) {
            Object title = firstNonNull(item.get("table_en_name"), item.get("table_cn_name"), item.get("sheet_name"), item.get("source_file"));
            if (title != null && !String.valueOf(title).trim().isEmpty()) {
                parts.add(String.valueOf(title));
            }
        }
        String base = parts.isEmpty() ? "未选择需求文件" : String.join("、", parts);
        if (!isBlank(request.sourceSamples)) {
            base += "\n来源表样例数据：前端兼容字段已忽略，系统将自动查询 MaxCompute";
        }
        if (!isBlank(request.notes)) {
            base += "\n备注：" + request.notes.trim().substring(0, Math.min(500, request.notes.trim().length()));
        }
        return base;
    }

    private String metricName(List<Map<String, Object>> requirements) {
        if (requirements.isEmpty()) {
            return "Dataphin SQL";
        }
        Map<String, Object> first = requirements.get(0);
        return String.valueOf(firstNonNull(first.get("table_en_name"), first.get("table_cn_name"), "Dataphin SQL"));
    }

    private Map<String, Object> provider() {
        Map<String, Object> provider = new LinkedHashMap<>();
        provider.put("provider_id", properties.getModel().getProviderId());
        provider.put("model", properties.getModel().getModelName());
        return provider;
    }

    private double elapsedMs(long startNs) {
        return Math.round(((System.nanoTime() - startNs) / 1_000_000.0D) * 100.0D) / 100.0D;
    }

    private static Object firstNonNull(Object... values) {
        for (Object value : values) {
            if (value != null) {
                return value;
            }
        }
        return null;
    }

    private static boolean isBlank(String value) {
        return value == null || value.trim().isEmpty();
    }

    private static Map<String, Object> mapOf(String key, Object value) {
        Map<String, Object> map = new LinkedHashMap<>();
        map.put(key, value);
        return map;
    }
}
