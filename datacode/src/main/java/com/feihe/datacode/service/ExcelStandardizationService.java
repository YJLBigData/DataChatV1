package com.feihe.datacode.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.feihe.datacode.config.DataCodeProperties;
import org.apache.poi.ss.usermodel.Cell;
import org.apache.poi.ss.usermodel.CellStyle;
import org.apache.poi.ss.usermodel.DataFormatter;
import org.apache.poi.ss.usermodel.Font;
import org.apache.poi.ss.usermodel.Row;
import org.apache.poi.ss.usermodel.Sheet;
import org.apache.poi.ss.usermodel.Workbook;
import org.apache.poi.ss.usermodel.WorkbookFactory;
import org.apache.poi.ss.util.WorkbookUtil;
import org.apache.poi.xssf.usermodel.XSSFWorkbook;
import org.springframework.stereotype.Service;

import java.io.ByteArrayInputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;

@Service
public class ExcelStandardizationService {
    private static final int MAX_ROWS_PER_SHEET = 600;
    private static final int MAX_COLUMNS_PER_ROW = 80;

    private final ModelClientService modelClientService;
    private final ObjectMapper mapper;
    private final DataCodeProperties properties;

    public ExcelStandardizationService(ModelClientService modelClientService, ObjectMapper mapper, DataCodeProperties properties) {
        this.modelClientService = modelClientService;
        this.mapper = mapper;
        this.properties = properties;
    }

    public Map<String, Object> standardize(String fileName, byte[] data, String promptText) throws Exception {
        Map<String, Object> workbookPayload = parseWorkbook(fileName, data);
        List<Map<String, String>> messages = modelClientService.buildExcelStandardizationMessages(promptText, workbookPayload);
        ModelClientService.CompletionResult completion = modelClientService.complete(messages);
        JsonNode normalized = mapper.readTree(extractJson(completion.getContent()));
        String outputName = normalized.path("file_name").asText("标准化结果.xlsx");
        if (!outputName.toLowerCase(Locale.ROOT).endsWith(".xlsx")) {
            outputName += ".xlsx";
        }
        String fileId = UUID.randomUUID().toString().replace("-", "");
        Path outputDir = normalizedDir();
        Files.createDirectories(outputDir);
        Path outputPath = outputDir.resolve(fileId + ".xlsx");
        writeWorkbook(normalized, outputPath);

        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("ok", true);
        payload.put("file_id", fileId);
        payload.put("file_name", sanitizeOutputName(outputName));
        payload.put("download_url", "/api/code/download-normalized/" + fileId);
        payload.put("sheet_count", normalized.path("sheets").isArray() ? normalized.path("sheets").size() : 0);
        payload.put("source_hash", sha1(data));
        return payload;
    }

    public Path resolveDownloadPath(String fileId) {
        if (fileId == null || !fileId.matches("[a-fA-F0-9]{32}")) {
            throw new IllegalArgumentException("文件ID不合法");
        }
        return normalizedDir().resolve(fileId + ".xlsx").normalize();
    }

    private Map<String, Object> parseWorkbook(String fileName, byte[] data) throws Exception {
        String suffix = suffix(fileName);
        if (!".xlsx".equals(suffix) && !".xlsm".equals(suffix) && !".xls".equals(suffix)) {
            throw new IllegalArgumentException("Excel 标准化仅支持 .xlsx/.xlsm/.xls 文件");
        }
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("source_file", fileName);
        payload.put("parse_mode", "plain_matrix");
        List<Map<String, Object>> sheets = new ArrayList<>();
        DataFormatter formatter = new DataFormatter(Locale.CHINA);
        try (Workbook workbook = WorkbookFactory.create(new ByteArrayInputStream(data))) {
            for (int idx = 0; idx < workbook.getNumberOfSheets(); idx++) {
                Sheet sheet = workbook.getSheetAt(idx);
                Map<String, Object> sheetPayload = new LinkedHashMap<>();
                sheetPayload.put("sheet_name", sheet.getSheetName());
                sheetPayload.put("last_row_num", sheet.getLastRowNum());
                List<List<String>> rows = new ArrayList<>();
                int lastRow = Math.min(sheet.getLastRowNum(), MAX_ROWS_PER_SHEET - 1);
                for (int rowIdx = 0; rowIdx <= lastRow; rowIdx++) {
                    Row row = sheet.getRow(rowIdx);
                    if (row == null) {
                        continue;
                    }
                    int lastCell = Math.min(Math.max(row.getLastCellNum(), 0), MAX_COLUMNS_PER_ROW);
                    List<String> values = new ArrayList<>();
                    boolean hasValue = false;
                    for (int colIdx = 0; colIdx < lastCell; colIdx++) {
                        Cell cell = row.getCell(colIdx);
                        String value = cleanCell(formatter.formatCellValue(cell));
                        if (!value.isEmpty()) {
                            hasValue = true;
                        }
                        values.add(value);
                    }
                    if (hasValue) {
                        rows.add(values);
                    }
                }
                sheetPayload.put("rows", rows);
                sheetPayload.put("truncated", sheet.getLastRowNum() + 1 > MAX_ROWS_PER_SHEET);
                sheets.add(sheetPayload);
            }
        }
        payload.put("sheets", sheets);
        return payload;
    }

    private void writeWorkbook(JsonNode normalized, Path outputPath) throws Exception {
        JsonNode sheets = normalized.path("sheets");
        if (!sheets.isArray() || sheets.size() == 0) {
            throw new IllegalArgumentException("模型未返回有效 sheets，无法生成 Excel");
        }
        try (Workbook workbook = new XSSFWorkbook()) {
            CellStyle headerStyle = workbook.createCellStyle();
            Font headerFont = workbook.createFont();
            headerFont.setBold(true);
            headerStyle.setFont(headerFont);
            int sheetIdx = 1;
            for (JsonNode sheetNode : sheets) {
                String sheetName = sheetNode.path("sheet_name").asText("Sheet" + sheetIdx);
                Sheet sheet = workbook.createSheet(WorkbookUtil.createSafeSheetName(trimSheetName(sheetName, sheetIdx)));
                List<String> headers = stringArray(sheetNode.path("headers"));
                Row headerRow = sheet.createRow(0);
                for (int col = 0; col < headers.size(); col++) {
                    Cell cell = headerRow.createCell(col);
                    cell.setCellValue(headers.get(col));
                    cell.setCellStyle(headerStyle);
                }
                JsonNode rows = sheetNode.path("rows");
                if (rows.isArray()) {
                    int rowNum = 1;
                    for (JsonNode rowNode : rows) {
                        Row row = sheet.createRow(rowNum++);
                        List<String> values = stringArray(rowNode);
                        for (int col = 0; col < headers.size(); col++) {
                            row.createCell(col).setCellValue(col < values.size() ? values.get(col) : "");
                        }
                    }
                }
                for (int col = 0; col < Math.min(headers.size(), 30); col++) {
                    sheet.autoSizeColumn(col);
                }
                sheetIdx++;
            }
            try (OutputStream out = Files.newOutputStream(outputPath)) {
                workbook.write(out);
            }
        }
    }

    private List<String> stringArray(JsonNode node) {
        List<String> values = new ArrayList<>();
        if (node == null || !node.isArray()) {
            return values;
        }
        for (JsonNode item : node) {
            values.add(cellValue(item));
        }
        return values;
    }

    private String extractJson(String content) {
        String text = content == null ? "" : content.trim();
        if (text.startsWith("```")) {
            text = text.replaceFirst("^```(?:json)?", "").replaceFirst("```$", "").trim();
        }
        int start = text.indexOf('{');
        int end = text.lastIndexOf('}');
        if (start >= 0 && end > start) {
            return text.substring(start, end + 1);
        }
        throw new IllegalArgumentException("模型未返回标准 JSON，无法生成 Excel");
    }

    private String cellValue(JsonNode node) {
        if (node == null || node.isNull()) {
            return "";
        }
        if (node.isValueNode()) {
            return node.asText("");
        }
        try {
            return mapper.writeValueAsString(node);
        } catch (Exception e) {
            return String.valueOf(node);
        }
    }

    private String trimSheetName(String sheetName, int fallbackIdx) {
        String text = sheetName == null || sheetName.trim().isEmpty() ? "Sheet" + fallbackIdx : sheetName.trim();
        return text.length() > 31 ? text.substring(0, 31) : text;
    }

    private Path normalizedDir() {
        return Paths.get(properties.getUploadDir()).toAbsolutePath().normalize().resolve("normalized");
    }

    private String sanitizeOutputName(String name) {
        StringBuilder builder = new StringBuilder();
        for (int i = 0; i < name.length() && builder.length() < 120; i++) {
            char ch = name.charAt(i);
            if (Character.isLetterOrDigit(ch) || ch == '.' || ch == '_' || ch == '-' || ch == ' ' || ch == '（' || ch == '）') {
                builder.append(ch);
            } else {
                builder.append('_');
            }
        }
        return builder.length() == 0 ? "标准化结果.xlsx" : builder.toString();
    }

    private String sha1(byte[] data) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-1");
        byte[] hash = digest.digest(data);
        StringBuilder builder = new StringBuilder();
        for (byte item : hash) {
            builder.append(String.format("%02x", item));
        }
        return builder.toString();
    }

    private String suffix(String fileName) {
        String text = fileName == null ? "" : fileName.toLowerCase(Locale.ROOT);
        int dot = text.lastIndexOf('.');
        return dot >= 0 ? text.substring(dot) : "";
    }

    private String cleanCell(String value) {
        return value == null ? "" : value.trim().replace("\r\n", "\n").replace("\r", "\n");
    }
}
