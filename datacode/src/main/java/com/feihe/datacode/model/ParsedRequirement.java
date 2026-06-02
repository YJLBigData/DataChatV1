package com.feihe.datacode.model;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public class ParsedRequirement {
    private String requirementId;
    private String sourceFile;
    private String sheetName;
    private String tableCnName = "";
    private String tableEnName = "";
    private String joinLogic = "";
    private List<Map<String, String>> fields = new ArrayList<>();
    private List<List<String>> rawRows = new ArrayList<>();

    public Map<String, Object> toPayload() {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("requirement_id", requirementId);
        payload.put("source_file", sourceFile);
        payload.put("sheet_name", sheetName);
        payload.put("table_cn_name", tableCnName);
        payload.put("table_en_name", tableEnName);
        payload.put("join_logic", joinLogic);
        payload.put("fields", fields);
        payload.put("raw_rows", rawRows);
        payload.put("content_markdown", renderMarkdown());
        return payload;
    }

    public String renderMarkdown() {
        StringBuilder builder = new StringBuilder();
        String title = firstNotBlank(tableCnName, sheetName, "需求");
        builder.append("## ").append(title).append("\n\n");
        builder.append("- 来源文件：").append(emptyIfNull(sourceFile)).append("\n");
        builder.append("- Sheet：").append(emptyIfNull(sheetName)).append("\n");
        if (isNotBlank(tableEnName)) {
            builder.append("- 目标表英文名：").append(tableEnName).append("\n");
        }
        if (isNotBlank(joinLogic)) {
            builder.append("\n### 关联逻辑\n").append(joinLogic).append("\n");
        }
        if (!fields.isEmpty()) {
            builder.append("\n### 字段需求\n");
            builder.append("| 序号 | 字段 | 类型 | 中文注释 | 来源表 | 来源字段 | 计算逻辑 | 备注 |\n");
            builder.append("|---|---|---|---|---|---|---|---|\n");
            for (Map<String, String> field : fields) {
                builder.append("| ")
                        .append(markdownCell(field.get("index"))).append(" | ")
                        .append(markdownCell(field.get("field_name"))).append(" | ")
                        .append(markdownCell(field.get("field_type"))).append(" | ")
                        .append(markdownCell(field.get("comment"))).append(" | ")
                        .append(markdownCell(field.get("source_table"))).append(" | ")
                        .append(markdownCell(field.get("source_field"))).append(" | ")
                        .append(markdownCell(field.get("calculation_logic"))).append(" | ")
                        .append(markdownCell(field.get("remark"))).append(" |\n");
            }
        }
        return builder.toString();
    }

    private static String markdownCell(String value) {
        return emptyIfNull(value).replace("|", "\\|").replace("\n", "<br>");
    }

    private static String firstNotBlank(String first, String second, String fallback) {
        if (isNotBlank(first)) {
            return first;
        }
        if (isNotBlank(second)) {
            return second;
        }
        return fallback;
    }

    private static boolean isNotBlank(String value) {
        return value != null && !value.trim().isEmpty();
    }

    private static String emptyIfNull(String value) {
        return value == null ? "" : value;
    }

    public String getRequirementId() {
        return requirementId;
    }

    public void setRequirementId(String requirementId) {
        this.requirementId = requirementId;
    }

    public String getSourceFile() {
        return sourceFile;
    }

    public void setSourceFile(String sourceFile) {
        this.sourceFile = sourceFile;
    }

    public String getSheetName() {
        return sheetName;
    }

    public void setSheetName(String sheetName) {
        this.sheetName = sheetName;
    }

    public String getTableCnName() {
        return tableCnName;
    }

    public void setTableCnName(String tableCnName) {
        this.tableCnName = tableCnName;
    }

    public String getTableEnName() {
        return tableEnName;
    }

    public void setTableEnName(String tableEnName) {
        this.tableEnName = tableEnName;
    }

    public String getJoinLogic() {
        return joinLogic;
    }

    public void setJoinLogic(String joinLogic) {
        this.joinLogic = joinLogic;
    }

    public List<Map<String, String>> getFields() {
        return fields;
    }

    public void setFields(List<Map<String, String>> fields) {
        this.fields = fields;
    }

    public List<List<String>> getRawRows() {
        return rawRows;
    }

    public void setRawRows(List<List<String>> rawRows) {
        this.rawRows = rawRows;
    }
}
