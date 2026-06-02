package com.feihe.datacode.service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.feihe.datacode.config.DataCodeProperties;
import okhttp3.MediaType;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.RequestBody;
import okhttp3.Response;
import okhttp3.ResponseBody;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@Service
public class ModelClientService {
    private static final MediaType JSON = MediaType.parse("application/json; charset=utf-8");
    private static final String SYSTEM_PROMPT = "你是资深数据开发专家，目标是根据业务需求生成 Dataphin/MaxCompute 可直接执行的开发 SQL。\n"
            + "必须遵守：\n"
            + "1. 只输出一个 SQL 代码块，不输出解释性正文。\n"
            + "2. SQL 必须包含 CREATE TABLE IF NOT EXISTS 和 INSERT OVERWRITE TABLE 两部分。\n"
            + "3. 默认目标表为分区表，分区字段固定为 ds，字段定义为 ds STRING COMMENT '业务日期'。\n"
            + "4. 写入语句默认使用 PARTITION (ds='${bizdate}')。\n"
            + "5. 所有源表查询默认按 ds='${bizdate}' 做分区过滤；如果源表没有 ds，需要在注释中明确说明原因。\n"
            + "6. 不允许 DROP/TRUNCATE/DELETE/UPDATE/GRANT/REVOKE 等高风险语句。\n"
            + "7. 字段顺序必须严格参考需求文件，目标字段必须显式列出，禁止 SELECT *。\n"
            + "8. 对需求中的疑问字段、枚举、退款、净额、净数量等口径必须用 CASE WHEN 或明确表达式实现。\n"
            + "9. 代码需要一键复制粘贴执行，中文注释和 COMMENT 保持清晰、专业、严谨。";
    private static final String EXCEL_NORMALIZE_PROMPT = "你是严谨的数据需求文档整理专家。你会收到一个从 Excel 纯解析出来的工作簿 JSON 和用户提示词，"
            + "需要把杂乱内容整理成标准 Excel 结构。只返回 JSON，不输出解释。JSON 格式必须为："
            + "{\"file_name\":\"标准化结果.xlsx\",\"sheets\":[{\"sheet_name\":\"Sheet1\",\"headers\":[\"列1\"],\"rows\":[[\"值1\"]]}]}。"
            + "headers 必须是字符串数组，rows 必须是二维字符串数组，行长度必须与 headers 对齐。";

    private final DataCodeProperties properties;
    private final ObjectMapper mapper;
    private final OkHttpClient httpClient;

    public ModelClientService(DataCodeProperties properties, ObjectMapper mapper) {
        this.properties = properties;
        this.mapper = mapper;
        this.httpClient = new OkHttpClient.Builder()
                .connectTimeout(Duration.ofSeconds(20))
                .readTimeout(Duration.ofMinutes(5))
                .writeTimeout(Duration.ofMinutes(2))
                .build();
    }

    public List<Map<String, String>> buildGenerationMessages(
            String promptMarkdown,
            List<Map<String, Object>> requirements,
            String sourceSchema,
            String sourceSamples,
            String notes,
            String dataphinContext) {
        StringBuilder requirementBlocks = new StringBuilder();
        int idx = 1;
        for (Map<String, Object> item : requirements) {
            Object title = firstNonNull(item.get("sheet_name"), item.get("source_file"), "需求");
            requirementBlocks.append("### 需求 ").append(idx++).append(": ").append(title).append("\n")
                    .append(firstNonNull(item.get("content_markdown"), item).toString()).append("\n\n");
        }

        String userContent = "# 用户模型提示词\n"
                + defaultString(promptMarkdown, "请根据需求生成严谨、可执行的 Dataphin SQL。") + "\n\n"
                + "# 业务需求文件解析结果\n"
                + requirementBlocks + "\n"
                + "# MaxCompute 自动查询的源表结构\n```text\n"
                + defaultString(sourceSchema, "-- 未查询到源表结构。") + "\n```\n\n"
                + "# MaxCompute 自动查询的来源表样例数据\n```text\n"
                + defaultString(sourceSamples, "未查询到来源表样例数据。") + "\n```\n\n"
                + "# Dataphin 自动上下文\n"
                + defaultString(dataphinContext, "未查询到自动上下文。") + "\n\n"
                + "# 备注\n"
                + defaultString(notes, "无") + "\n\n"
                + "# 输出要求\n"
                + "请输出 Dataphin/MaxCompute SQL，包含建表语句和写入语句。默认分区查询与写入均使用 ds='${bizdate}'。"
                + "来源表样例数据通常是一行查询语句加多行实际结果，只用于理解样例格式、字段数量、数据类型、日期格式和可能的枚举形态，"
                + "不要关注样例里的具体业务值，不允许把样例值硬编码为过滤条件，除非备注或需求文件明确要求。";
        List<Map<String, String>> messages = new ArrayList<>();
        messages.add(message("system", SYSTEM_PROMPT));
        messages.add(message("user", userContent));
        return messages;
    }

    public List<Map<String, String>> buildRepairMessages(String sql, List<String> validationErrors, List<Map<String, String>> originalMessages) {
        String originalContext = originalMessages.isEmpty() ? "" : originalMessages.get(originalMessages.size() - 1).get("content");
        String content = "以下 SQL 校验失败，请只返回修复后的完整 SQL 代码块。\n\n"
                + "## 校验错误\n- " + String.join("\n- ", validationErrors) + "\n\n"
                + "## 原始需求上下文\n" + originalContext + "\n\n"
                + "## 待修复 SQL\n```sql\n" + sql + "\n```";
        List<Map<String, String>> messages = new ArrayList<>();
        messages.add(message("system", SYSTEM_PROMPT));
        messages.add(message("user", content));
        return messages;
    }

    public List<Map<String, String>> buildExcelStandardizationMessages(String promptText, Map<String, Object> workbookPayload) throws Exception {
        String content = "# 用户整理要求\n"
                + defaultString(promptText, "请把 Excel 内容整理成字段清晰、表头规范、可直接交付的标准 Excel。") + "\n\n"
                + "# Excel 纯解析结果\n```json\n"
                + mapper.writeValueAsString(workbookPayload)
                + "\n```\n\n"
                + "# 输出要求\n"
                + "只返回 JSON。不要返回 Markdown，不要包裹代码块。file_name 使用 .xlsx 后缀。"
                + "如果原始文件有多个业务块，可以拆成多个 sheet；如果无法判断，保留一个 sheet。";
        List<Map<String, String>> messages = new ArrayList<>();
        messages.add(message("system", EXCEL_NORMALIZE_PROMPT));
        messages.add(message("user", content));
        return messages;
    }

    public CompletionResult complete(List<Map<String, String>> messages) throws Exception {
        DataCodeProperties.Model model = properties.getModel();
        if (isBlank(model.getApiKey())) {
            throw new IllegalStateException("未配置 DATACODE_LLM_API_KEY / DASHSCOPE_API_KEY / QWEN_API_KEY");
        }
        Map<String, Object> requestPayload = new LinkedHashMap<>();
        requestPayload.put("model", model.getModelName());
        requestPayload.put("messages", messages);
        requestPayload.put("temperature", model.getTemperature());
        requestPayload.put("max_tokens", model.getMaxTokens());
        String body = mapper.writeValueAsString(requestPayload);
        Request request = new Request.Builder()
                .url(chatCompletionsUrl(model.getBaseUrl()))
                .addHeader("Authorization", "Bearer " + model.getApiKey())
                .addHeader("Content-Type", "application/json")
                .post(RequestBody.create(body, JSON))
                .build();
        try (Response response = httpClient.newCall(request).execute()) {
            ResponseBody responseBody = response.body();
            String responseText = responseBody == null ? "" : responseBody.string();
            if (!response.isSuccessful()) {
                throw new IllegalStateException("模型调用失败: HTTP " + response.code() + " " + responseText);
            }
            JsonNode root = mapper.readTree(responseText);
            String content = root.path("choices").path(0).path("message").path("content").asText("");
            Map<String, Object> responsePayload = mapper.convertValue(root, new TypeReference<Map<String, Object>>() {
            });
            return new CompletionResult(content, responsePayload, requestPayload);
        }
    }

    public Map<String, Object> providerPayload() {
        Map<String, Object> provider = new LinkedHashMap<>();
        provider.put("provider_id", properties.getModel().getProviderId());
        provider.put("label", "Qwen Dataphin SQL");
        provider.put("model", properties.getModel().getModelName());
        provider.put("base_url", properties.getModel().getBaseUrl());
        provider.put("enabled", true);
        return provider;
    }

    private String chatCompletionsUrl(String baseUrl) {
        String base = defaultString(baseUrl, "https://dashscope.aliyuncs.com/compatible-mode/v1").trim();
        if (base.endsWith("/chat/completions")) {
            return base;
        }
        while (base.endsWith("/")) {
            base = base.substring(0, base.length() - 1);
        }
        return base + "/chat/completions";
    }

    private static Map<String, String> message(String role, String content) {
        Map<String, String> message = new LinkedHashMap<>();
        message.put("role", role);
        message.put("content", content);
        return message;
    }

    private static Object firstNonNull(Object... values) {
        for (Object value : values) {
            if (value != null) {
                return value;
            }
        }
        return "";
    }

    private static String defaultString(String value, String fallback) {
        return isBlank(value) ? fallback : value;
    }

    private static boolean isBlank(String value) {
        return value == null || value.trim().isEmpty();
    }

    public static class CompletionResult {
        private final String content;
        private final Map<String, Object> responsePayload;
        private final Map<String, Object> requestPayload;

        public CompletionResult(String content, Map<String, Object> responsePayload, Map<String, Object> requestPayload) {
            this.content = content;
            this.responsePayload = responsePayload;
            this.requestPayload = requestPayload;
        }

        public String getContent() {
            return content;
        }

        public Map<String, Object> getResponsePayload() {
            return responsePayload;
        }

        public Map<String, Object> getRequestPayload() {
            return requestPayload;
        }
    }
}
