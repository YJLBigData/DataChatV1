package com.feihe.datacode.service;

import com.feihe.datacode.model.ParsedRequirement;
import org.apache.poi.ss.usermodel.Cell;
import org.apache.poi.ss.usermodel.ClientAnchor;
import org.apache.poi.ss.usermodel.DataFormatter;
import org.apache.poi.ss.usermodel.Drawing;
import org.apache.poi.ss.usermodel.Picture;
import org.apache.poi.ss.usermodel.Row;
import org.apache.poi.ss.usermodel.Shape;
import org.apache.poi.ss.usermodel.Sheet;
import org.apache.poi.ss.usermodel.Workbook;
import org.apache.poi.ss.usermodel.WorkbookFactory;
import org.springframework.stereotype.Service;

import java.io.ByteArrayInputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

@Service
public class RequirementParserService {
    private static final Map<String, String> KEY_ALIASES = new LinkedHashMap<>();
    private static final Map<String, String> FIELD_ALIASES = new LinkedHashMap<>();
    private static final Pattern KEY_CLEAN_PATTERN = Pattern.compile("[:：\\s]");

    static {
        KEY_ALIASES.put("表中文名称", "table_cn_name");
        KEY_ALIASES.put("表英文名称", "table_en_name");
        KEY_ALIASES.put("关联逻辑", "join_logic");
        KEY_ALIASES.put("逻辑说明", "join_logic");

        FIELD_ALIASES.put("序号", "index");
        FIELD_ALIASES.put("字段", "field_name");
        FIELD_ALIASES.put("字段名", "field_name");
        FIELD_ALIASES.put("目标表字段", "field_name");
        FIELD_ALIASES.put("目标字段", "field_name");
        FIELD_ALIASES.put("字段类型", "field_type");
        FIELD_ALIASES.put("目标表字段类型", "field_type");
        FIELD_ALIASES.put("中文注释", "comment");
        FIELD_ALIASES.put("字段中文名", "comment");
        FIELD_ALIASES.put("目标表中文注释", "comment");
        FIELD_ALIASES.put("来源表", "source_table");
        FIELD_ALIASES.put("来源字段", "source_field");
        FIELD_ALIASES.put("计算逻辑", "calculation_logic");
        FIELD_ALIASES.put("口径逻辑", "calculation_logic");
        FIELD_ALIASES.put("备注", "remark");
    }

    public List<Map<String, Object>> parse(String fileName, byte[] data) throws Exception {
        String suffix = suffix(fileName);
        List<ParsedRequirement> parsed;
        if (Arrays.asList(".xlsx", ".xlsm", ".xls").contains(suffix)) {
            parsed = parseWorkbook(fileName, data);
        } else if (".csv".equals(suffix)) {
            parsed = new ArrayList<>();
            parsed.add(parseRows(fileName, removeSuffix(fileName), trimMatrix(parseCsv(data))));
        } else {
            throw new IllegalArgumentException("仅支持 .xlsx/.xlsm/.xls/.csv 需求文件");
        }
        List<Map<String, Object>> payload = new ArrayList<>();
        for (ParsedRequirement requirement : parsed) {
            payload.add(requirement.toPayload());
        }
        return payload;
    }

    private List<ParsedRequirement> parseWorkbook(String fileName, byte[] data) throws Exception {
        List<ParsedRequirement> output = new ArrayList<>();
        DataFormatter formatter = new DataFormatter(Locale.CHINA);
        try (Workbook workbook = WorkbookFactory.create(new ByteArrayInputStream(data))) {
            for (int idx = 0; idx < workbook.getNumberOfSheets(); idx++) {
                Sheet sheet = workbook.getSheetAt(idx);
                Set<Integer> imageRows = rowsWithPictures(sheet);
                List<List<String>> rows = new ArrayList<>();
                int lastRow = Math.max(sheet.getLastRowNum(), 0);
                for (int rowIdx = 0; rowIdx <= lastRow; rowIdx++) {
                    if (imageRows.contains(rowIdx)) {
                        continue;
                    }
                    Row row = sheet.getRow(rowIdx);
                    List<String> values = new ArrayList<>();
                    if (row != null) {
                        short lastCell = row.getLastCellNum();
                        for (int colIdx = 0; colIdx < Math.max(lastCell, 0); colIdx++) {
                            Cell cell = row.getCell(colIdx);
                            values.add(cleanCell(formatter.formatCellValue(cell)));
                        }
                    }
                    rows.add(values);
                }
                output.add(parseRows(fileName, sheet.getSheetName(), trimMatrix(rows)));
            }
        }
        return output;
    }

    private Set<Integer> rowsWithPictures(Sheet sheet) {
        Set<Integer> rows = new HashSet<>();
        Drawing<?> drawing = sheet.getDrawingPatriarch();
        if (drawing == null) {
            return rows;
        }
        for (Shape shape : drawing) {
            if (!(shape instanceof Picture)) {
                continue;
            }
            if (((Picture) shape).getClientAnchor() == null) {
                continue;
            }
            ClientAnchor anchor = ((Picture) shape).getClientAnchor();
            int row1 = Math.max(anchor.getRow1(), 0);
            int row2 = Math.max(anchor.getRow2(), row1);
            rows.add(row1);
            for (int rowIdx = row1; rowIdx <= row2; rowIdx++) {
                rows.add(rowIdx);
            }
        }
        return rows;
    }

    private ParsedRequirement parseRows(String fileName, String sheetName, List<List<String>> rows) throws Exception {
        Map<String, String> meta = new LinkedHashMap<>();
        int headerIdx = -1;
        Map<Integer, String> headerMap = new LinkedHashMap<>();

        for (int idx = 0; idx < rows.size(); idx++) {
            List<String> row = rows.get(idx);
            for (int colIdx = 0; colIdx < row.size(); colIdx++) {
                String key = cleanKey(row.get(colIdx));
                if (KEY_ALIASES.containsKey(key)) {
                    String value = "";
                    for (int nextIdx = colIdx + 1; nextIdx < row.size(); nextIdx++) {
                        if (!row.get(nextIdx).trim().isEmpty()) {
                            value = row.get(nextIdx).trim();
                            break;
                        }
                    }
                    meta.put(KEY_ALIASES.get(key), value);
                }
            }
            Map<Integer, String> matched = new LinkedHashMap<>();
            for (int colIdx = 0; colIdx < row.size(); colIdx++) {
                String key = cleanKey(row.get(colIdx));
                if (FIELD_ALIASES.containsKey(key)) {
                    matched.put(colIdx, FIELD_ALIASES.get(key));
                }
            }
            if (matched.containsValue("field_name") && matched.size() >= 3) {
                headerIdx = idx;
                headerMap = matched;
                break;
            }
        }

        List<Map<String, String>> fields = new ArrayList<>();
        if (headerIdx >= 0) {
            for (int rowIdx = headerIdx + 1; rowIdx < rows.size(); rowIdx++) {
                List<String> row = rows.get(rowIdx);
                Map<String, String> item = new LinkedHashMap<>();
                for (Map.Entry<Integer, String> entry : headerMap.entrySet()) {
                    String value = entry.getKey() < row.size() ? row.get(entry.getKey()).trim() : "";
                    item.put(entry.getValue(), value);
                }
                if (item.values().stream().noneMatch(value -> value != null && !value.trim().isEmpty())) {
                    continue;
                }
                if (isBlank(item.get("field_name")) && isBlank(item.get("comment")) && isBlank(item.get("calculation_logic"))) {
                    continue;
                }
                fields.add(item);
            }
        }
        if (fields.isEmpty()) {
            for (List<String> row : rows) {
                List<String> parts = new ArrayList<>();
                for (String cell : row) {
                    if (!isBlank(cell)) {
                        parts.add(cell);
                    }
                }
                if (!parts.isEmpty()) {
                    Map<String, String> item = new LinkedHashMap<>();
                    item.put("raw", String.join(" | ", parts));
                    fields.add(item);
                }
            }
        }

        ParsedRequirement requirement = new ParsedRequirement();
        requirement.setRequirementId(requirementId(fileName, sheetName, rows));
        requirement.setSourceFile(fileName);
        requirement.setSheetName(sheetName);
        requirement.setTableCnName(meta.getOrDefault("table_cn_name", ""));
        requirement.setTableEnName(meta.getOrDefault("table_en_name", ""));
        requirement.setJoinLogic(meta.getOrDefault("join_logic", ""));
        requirement.setFields(fields);
        requirement.setRawRows(rows.subList(0, Math.min(rows.size(), 80)));
        return requirement;
    }

    private List<List<String>> parseCsv(byte[] data) {
        String text = new String(data, StandardCharsets.UTF_8);
        if (text.startsWith("\uFEFF")) {
            text = text.substring(1);
        }
        List<List<String>> rows = new ArrayList<>();
        List<String> row = new ArrayList<>();
        StringBuilder cell = new StringBuilder();
        boolean inQuotes = false;
        for (int i = 0; i < text.length(); i++) {
            char ch = text.charAt(i);
            if (ch == '"') {
                if (inQuotes && i + 1 < text.length() && text.charAt(i + 1) == '"') {
                    cell.append('"');
                    i++;
                } else {
                    inQuotes = !inQuotes;
                }
            } else if (ch == ',' && !inQuotes) {
                row.add(cleanCell(cell.toString()));
                cell.setLength(0);
            } else if ((ch == '\n' || ch == '\r') && !inQuotes) {
                if (ch == '\r' && i + 1 < text.length() && text.charAt(i + 1) == '\n') {
                    i++;
                }
                row.add(cleanCell(cell.toString()));
                rows.add(row);
                row = new ArrayList<>();
                cell.setLength(0);
            } else {
                cell.append(ch);
            }
        }
        row.add(cleanCell(cell.toString()));
        rows.add(row);
        return rows;
    }

    private List<List<String>> trimMatrix(List<List<String>> rows) {
        List<List<String>> output = new ArrayList<>(rows);
        while (!output.isEmpty() && output.get(output.size() - 1).stream().noneMatch(value -> !isBlank(value))) {
            output.remove(output.size() - 1);
        }
        int maxLen = 0;
        for (List<String> row : output) {
            maxLen = Math.max(maxLen, row.size());
        }
        for (List<String> row : output) {
            while (row.size() < maxLen) {
                row.add("");
            }
        }
        while (!output.isEmpty() && output.get(0).stream().noneMatch(value -> !isBlank(value))) {
            output.remove(0);
        }
        return output;
    }

    private String requirementId(String fileName, String sheetName, List<List<String>> rows) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-1");
        String seed = fileName + "\n" + sheetName + "\n" + rows.subList(0, Math.min(rows.size(), 12));
        byte[] hash = digest.digest(seed.getBytes(StandardCharsets.UTF_8));
        StringBuilder builder = new StringBuilder();
        for (byte item : hash) {
            builder.append(String.format("%02x", item));
        }
        return builder.substring(0, 16);
    }

    private String cleanKey(String value) {
        return KEY_CLEAN_PATTERN.matcher(cleanCell(value)).replaceAll("");
    }

    private String cleanCell(String value) {
        return value == null ? "" : value.trim().replace("\r\n", "\n").replace("\r", "\n");
    }

    private String suffix(String fileName) {
        String text = fileName == null ? "" : fileName.toLowerCase(Locale.ROOT);
        int dot = text.lastIndexOf('.');
        return dot >= 0 ? text.substring(dot) : "";
    }

    private String removeSuffix(String fileName) {
        String text = fileName == null ? "CSV" : fileName;
        int dot = text.lastIndexOf('.');
        return dot > 0 ? text.substring(0, dot) : text;
    }

    private boolean isBlank(String value) {
        return value == null || value.trim().isEmpty();
    }
}
