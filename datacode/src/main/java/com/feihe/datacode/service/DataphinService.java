package com.feihe.datacode.service;

import com.feihe.datacode.config.DataCodeProperties;
import com.feihe.datacode.model.DataphinQueryResult;
import org.springframework.stereotype.Service;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.ResultSetMetaData;
import java.time.LocalDate;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Properties;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

@Service
public class DataphinService {
    private static final List<String> PROJECTS = Arrays.asList(
            "firmus_dataphin_prd_ods",
            "firmus_dataphin_prd_cdm",
            "firmus_dataphin_prd_ads",
            "firmus_dataphin_prd_ods_dev",
            "firmus_dataphin_prd_cdm_dev",
            "firmus_dataphin_prd_ads_dev",
            "firmus_ods_hh",
            "firmus_cdm_hh",
            "firmus_ads_hh",
            "firmus_ods_hh_dev",
            "firmus_cdm_hh_dev",
            "firmus_ads_hh_dev"
    );
    private static final Pattern READONLY = Pattern.compile("^\\s*(select|show|desc|describe|explain)\\b", Pattern.CASE_INSENSITIVE);
    private static final Pattern FORBIDDEN = Pattern.compile("\\b(insert|overwrite|update|delete|drop|truncate|alter|create|grant|revoke|merge|call|set)\\b", Pattern.CASE_INSENSITIVE);
    private static final Pattern SQL_TABLE_REF = Pattern.compile("\\b(?:from|join)\\s+([a-zA-Z_][\\w]*(?:\\.[a-zA-Z_][\\w]*)?)", Pattern.CASE_INSENSITIVE);
    private static final Pattern TABLE_IDENTIFIER = Pattern.compile("[a-zA-Z_][\\w]*(?:\\.[a-zA-Z_][\\w]*)?");
    private static final Set<String> SQL_KEYWORDS = new LinkedHashSet<>(Arrays.asList(
            "select", "from", "join", "where", "left", "right", "inner", "outer", "full", "cross",
            "on", "and", "or", "case", "when", "then", "else", "end", "as", "group", "order",
            "by", "limit", "partition", "table", "desc", "describe", "show", "ds"));

    private final DataCodeProperties properties;

    public DataphinService(DataCodeProperties properties) {
        this.properties = properties;
    }

    public Map<String, Object> config() {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("endpoint", properties.getMaxcompute().getEndpoint());
        payload.put("default_project", properties.getMaxcompute().getDefaultProject());
        payload.put("projects", PROJECTS);
        payload.put("bizdate", defaultBizdate());
        payload.put("credentials_configured", !isBlank(properties.getMaxcompute().getAccessKeyId())
                && !isBlank(properties.getMaxcompute().getAccessKeySecret()));
        payload.put("jdbc_configured", payload.get("credentials_configured"));
        payload.put("endpoint_configured", !isBlank(properties.getMaxcompute().getEndpoint()));
        payload.put("tenant_configured", true);
        payload.put("region_id", "maxcompute-cn-beijing");
        return payload;
    }

    public DataphinQueryResult executeDataphinQuery(String sql, String projectName, Integer limit, String bizdate, boolean enforceReadonly) throws Exception {
        String project = normalizeProject(projectName);
        int maxRows = normalizeLimit(limit, 100, properties.getMaxcompute().getReadonlyMaxLimit());
        String renderedSql = renderBizdate(sql, bizdate);
        if (enforceReadonly) {
            renderedSql = ensureReadonlySql(renderedSql);
        }
        renderedSql = appendLimitIfNeeded(renderedSql, maxRows);
        ensureCredentials();
        Class.forName("com.aliyun.odps.jdbc.OdpsDriver");
        Properties props = new Properties();
        props.put("access_id", properties.getMaxcompute().getAccessKeyId());
        props.put("access_key", properties.getMaxcompute().getAccessKeySecret());
        String jdbcUrl = "jdbc:odps:" + properties.getMaxcompute().getEndpoint() + "?project=" + project;
        try (Connection conn = DriverManager.getConnection(jdbcUrl, props);
             PreparedStatement ps = conn.prepareStatement(renderedSql);
             ResultSet rs = ps.executeQuery()) {
            DataphinQueryResult result = new DataphinQueryResult();
            result.setProject(project);
            result.setSql(renderedSql);
            ResultSetMetaData metaData = rs.getMetaData();
            int columnCount = metaData.getColumnCount();
            List<Map<String, String>> columns = new ArrayList<>();
            for (int i = 1; i <= columnCount; i++) {
                Map<String, String> column = new LinkedHashMap<>();
                column.put("name", metaData.getColumnLabel(i));
                column.put("type", metaData.getColumnTypeName(i));
                columns.add(column);
            }
            List<List<String>> rows = new ArrayList<>();
            while (rs.next() && rows.size() < maxRows) {
                List<String> row = new ArrayList<>();
                for (int i = 1; i <= columnCount; i++) {
                    Object value = rs.getObject(i);
                    row.add(value == null ? null : String.valueOf(value));
                }
                rows.add(row);
            }
            result.setColumns(columns);
            result.setRows(rows);
            result.setRowCount(rows.size());
            return result;
        }
    }

    public DataphinQueryResult queryTaskNodes(String projectName, String keyword, String operatorType, String nodeType, Integer limit, String bizdate) throws Exception {
        List<String> conditions = new ArrayList<>();
        conditions.add("ds = '${bizdate}'");
        if (!isBlank(projectName)) {
            conditions.add("project_name = '" + escapeSqlLiteral(normalizeProject(projectName)) + "'");
        }
        if (!isBlank(keyword)) {
            String text = escapeSqlLiteral(keyword.trim());
            conditions.add("(node_name LIKE '%" + text + "%' OR content LIKE '%" + text + "%' OR node_id LIKE '%" + text + "%')");
        }
        if (!isBlank(operatorType)) {
            conditions.add("operator_type = '" + escapeSqlLiteral(operatorType.trim()) + "'");
        }
        if (!isBlank(nodeType)) {
            conditions.add("node_type = '" + escapeSqlLiteral(nodeType.trim()) + "'");
        }
        String sql = "SELECT project_name,node_name,owner_name,modifier_name,node_type,operator_type,\n"
                + "       cron_expression,schedule_interval_type,directorys,content,gmt_create,gmt_modified,\n"
                + "       direc_lev1,direc_lev2,direc_lev3,direc_lev4,direc_lev5,is_downstream,param,node_id,ds\n"
                + "  FROM firmus_dataphin_prd_ads.ads_dataphin_vdm_node_detail\n"
                + " WHERE " + String.join(" AND ", conditions) + "\n"
                + " ORDER BY gmt_modified DESC";
        return executeDataphinQuery(sql, properties.getMaxcompute().getDefaultProject(), limit, bizdate, true);
    }

    public DataphinQueryResult queryTableLineage(String tableName, String projectName, String direction, Integer limit, String bizdate) throws Exception {
        String table = escapeSqlLiteral(tableName == null ? "" : tableName.trim());
        if (table.isEmpty()) {
            throw new IllegalArgumentException("table_name 不能为空");
        }
        List<String> conditions = new ArrayList<>();
        conditions.add("ds = '${bizdate}'");
        String dir = direction == null ? "both" : direction.trim().toLowerCase(Locale.ROOT);
        if ("upstream".equals(dir)) {
            conditions.add("target_table = '" + table + "'");
        } else if ("downstream".equals(dir)) {
            conditions.add("source_table = '" + table + "'");
        } else {
            conditions.add("(source_table = '" + table + "' OR target_table = '" + table + "')");
        }
        if (!isBlank(projectName)) {
            String project = escapeSqlLiteral(normalizeProject(projectName));
            conditions.add("(source_project = '" + project + "' OR target_project = '" + project + "')");
        }
        String sql = "SELECT id,source_project,source_table,source_table_original,\n"
                + "       target_project,target_table,target_table_original,\n"
                + "       target_owner_name,target_modifier_name,target_gmt_create,target_gmt_modified,\n"
                + "       time_wimdow,strategy,source_node_id,target_node_id,ds\n"
                + "  FROM firmus_dataphin_prd_ads.ads_dataphin_table_blood_relationship\n"
                + " WHERE " + String.join(" AND ", conditions) + "\n"
                + " ORDER BY target_gmt_modified DESC";
        return executeDataphinQuery(sql, properties.getMaxcompute().getDefaultProject(), limit, bizdate, true);
    }

    public DataphinQueryResult queryTaskLineage(String nodeId, String direction, Integer limit, String bizdate) throws Exception {
        String node = escapeSqlLiteral(nodeId == null ? "" : nodeId.trim());
        if (node.isEmpty()) {
            throw new IllegalArgumentException("node_id 不能为空");
        }
        List<String> conditions = new ArrayList<>();
        conditions.add("ds = '${bizdate}'");
        String dir = direction == null ? "both" : direction.trim().toLowerCase(Locale.ROOT);
        if ("upstream".equals(dir)) {
            conditions.add("target_node_id = '" + node + "'");
        } else if ("downstream".equals(dir)) {
            conditions.add("source_node_id = '" + node + "'");
        } else {
            conditions.add("(source_node_id = '" + node + "' OR target_node_id = '" + node + "')");
        }
        String sql = "SELECT id,source_project,source_table,target_project,target_table,\n"
                + "       target_owner_name,target_modifier_name,time_wimdow,strategy,\n"
                + "       source_node_id,target_node_id,ds\n"
                + "  FROM firmus_dataphin_prd_ads.ads_dataphin_table_blood_relationship\n"
                + " WHERE " + String.join(" AND ", conditions) + "\n"
                + " ORDER BY target_table ASC";
        return executeDataphinQuery(sql, properties.getMaxcompute().getDefaultProject(), limit, bizdate, true);
    }

    public SourceMetadata collectRequiredSourceMetadata(List<Map<String, Object>> requirements, String notes) throws Exception {
        List<String> tableRefs = extractSourceTableReferences(requirements, notes, 30);
        if (tableRefs.isEmpty()) {
            throw new IllegalArgumentException("需求文件中没有解析到来源表，无法自动查询源表结构和样例数据。请检查需求文件的“来源表”列或在备注中补充 from/join 表名。");
        }

        StringBuilder schema = new StringBuilder();
        StringBuilder samples = new StringBuilder();
        List<String> resolvedTables = new ArrayList<>();
        List<String> missingTables = new ArrayList<>();
        List<String> errors = new ArrayList<>();
        for (String tableRef : tableRefs) {
            try {
                SourceTableMetadata metadata = querySourceTableMetadata(tableRef, null);
                resolvedTables.add(metadata.qualifiedName);
                schema.append("\n\n## ").append(metadata.qualifiedName).append("\n")
                        .append(metadata.schemaText);
                samples.append("\n\n## ").append(metadata.qualifiedName).append("\n")
                        .append(metadata.sampleText);
            } catch (Exception e) {
                missingTables.add(tableRef);
                errors.add(tableRef + ": " + e.getMessage());
            }
        }
        if (!missingTables.isEmpty()) {
            throw new IllegalArgumentException("找不到源表或无法从 MaxCompute 查询源表信息：" + String.join("、", missingTables)
                    + "。请检查需求文件中的来源表名称。详细错误：" + String.join("；", errors));
        }
        return new SourceMetadata(resolvedTables, schema.toString().trim(), samples.toString().trim());
    }

    public String collectGenerationDataphinContext(List<Map<String, Object>> requirements, String sourceSchema, String sourceSamples, String notes) {
        if ("false".equalsIgnoreCase(System.getenv().getOrDefault("DATAPHIN_AUTO_CONTEXT", "true"))
                || "0".equals(System.getenv().getOrDefault("DATAPHIN_AUTO_CONTEXT", "true"))) {
            return "";
        }
        if (isBlank(properties.getMaxcompute().getAccessKeyId()) || isBlank(properties.getMaxcompute().getAccessKeySecret())) {
            return "";
        }
        List<String> tableRefs = extractTableReferences(requirements, sourceSchema, sourceSamples, notes, 5);
        if (tableRefs.isEmpty()) {
            return "";
        }
        StringBuilder blocks = new StringBuilder("# Dataphin 自动上下文\n")
                .append("以下内容由平台根据表名自动查询 Dataphin 元数据，仅作为生成 SQL 的辅助依据。");
        for (String tableRef : tableRefs) {
            String table = tableRef.contains(".") ? tableRef.substring(tableRef.lastIndexOf('.') + 1) : tableRef;
            String project = tableRef.contains(".") ? tableRef.substring(0, tableRef.lastIndexOf('.')) : null;
            String projectFilter = PROJECTS.contains(project) ? project : null;
            blocks.append("\n\n## 表 ").append(tableRef).append("\n");
            try {
                DataphinQueryResult tasks = queryTaskNodes(projectFilter, table, null, null, 5, null);
                List<Map<String, String>> taskRows = rowsToDicts(tasks);
                if (!taskRows.isEmpty()) {
                    blocks.append("### 相关任务代码\n");
                    for (Map<String, String> item : taskRows) {
                        String header = joinNonBlank(" / ", item.get("project_name"), item.get("node_name"), item.get("operator_type"), item.get("node_id"));
                        String content = item.getOrDefault("content", "").trim();
                        if (content.length() > 1800) {
                            content = content.substring(0, 1800) + "\n-- 内容过长，已截断";
                        }
                        blocks.append("- ").append(header).append("\n```sql\n")
                                .append(content.isEmpty() ? "-- 无任务代码" : content)
                                .append("\n```\n");
                    }
                }
            } catch (Exception e) {
                blocks.append("### 相关任务代码\n- 查询失败：").append(e.getMessage()).append("\n");
            }
            try {
                DataphinQueryResult lineage = queryTableLineage(table, projectFilter, "both", 20, null);
                List<Map<String, String>> lineageRows = rowsToDicts(lineage);
                if (!lineageRows.isEmpty()) {
                    blocks.append("### 表级血缘\n");
                    for (Map<String, String> item : lineageRows) {
                        String source = item.get("source_project") + "." + item.get("source_table");
                        String target = item.get("target_project") + "." + item.get("target_table");
                        blocks.append("- ").append(source).append(" -> ").append(target)
                                .append("；负责人：").append(item.getOrDefault("target_owner_name", ""))
                                .append("；节点：").append(item.getOrDefault("source_node_id", ""))
                                .append(" -> ").append(item.getOrDefault("target_node_id", ""))
                                .append("\n");
                    }
                }
            } catch (Exception e) {
                blocks.append("### 表级血缘\n- 查询失败：").append(e.getMessage()).append("\n");
            }
        }
        return blocks.toString().trim();
    }

    public List<String> extractTableReferences(List<Map<String, Object>> requirements, String sourceSchema, String sourceSamples, String notes, int limit) {
        Set<String> candidates = new LinkedHashSet<>();
        addRefsFromText(candidates, sourceSchema);
        addRefsFromText(candidates, sourceSamples);
        addRefsFromText(candidates, notes);
        walkRequirementRefs(candidates, requirements);
        List<String> output = new ArrayList<>();
        for (String candidate : candidates) {
            output.add(candidate);
            if (output.size() >= limit) {
                break;
            }
        }
        return output;
    }

    public List<String> extractSourceTableReferences(List<Map<String, Object>> requirements, String notes, int limit) {
        Set<String> candidates = new LinkedHashSet<>();
        walkSourceRefs(candidates, requirements);
        addRefsFromText(candidates, notes);
        List<String> output = new ArrayList<>();
        for (String candidate : candidates) {
            output.add(candidate);
            if (output.size() >= limit) {
                break;
            }
        }
        return output;
    }

    private SourceTableMetadata querySourceTableMetadata(String tableRef, String bizdate) throws Exception {
        String normalizedRef = normalizeTableIdentifier(tableRef);
        Exception lastError = null;
        for (String candidate : tableCandidates(normalizedRef)) {
            try {
                DataphinQueryResult schemaResult = executeDataphinQuery(
                        "DESC " + candidate,
                        properties.getMaxcompute().getDefaultProject(),
                        800,
                        bizdate,
                        true);
                boolean hasDs = hasColumn(schemaResult, "ds");
                String sampleSql = "SELECT * FROM " + candidate + (hasDs ? " WHERE ds = '${bizdate}'" : "") + " LIMIT 5";
                DataphinQueryResult sampleResult = executeDataphinQuery(
                        sampleSql,
                        properties.getMaxcompute().getDefaultProject(),
                        5,
                        bizdate,
                        true);
                return new SourceTableMetadata(candidate, renderResult(schemaResult), renderResult(sampleResult));
            } catch (Exception e) {
                lastError = e;
            }
        }
        throw lastError == null ? new IllegalArgumentException("无法识别表名: " + tableRef) : lastError;
    }

    private List<String> tableCandidates(String tableRef) {
        List<String> candidates = new ArrayList<>();
        if (tableRef.contains(".")) {
            candidates.add(tableRef);
            return candidates;
        }
        addCandidate(candidates, properties.getMaxcompute().getDefaultProject() + "." + tableRef);
        for (String project : PROJECTS) {
            addCandidate(candidates, project + "." + tableRef);
        }
        return candidates;
    }

    private void addCandidate(List<String> candidates, String table) {
        if (!candidates.contains(table)) {
            candidates.add(table);
        }
    }

    private boolean hasColumn(DataphinQueryResult result, String columnName) {
        String expected = columnName == null ? "" : columnName.trim().toLowerCase(Locale.ROOT);
        for (List<String> row : result.getRows()) {
            for (String cell : row) {
                if (expected.equals(nullToEmpty(cell).trim().toLowerCase(Locale.ROOT))) {
                    return true;
                }
            }
        }
        return false;
    }

    private String renderResult(DataphinQueryResult result) {
        StringBuilder builder = new StringBuilder();
        builder.append("执行SQL：").append(result.getSql()).append("\n");
        List<String> headers = new ArrayList<>();
        for (Map<String, String> column : result.getColumns()) {
            headers.add(column.getOrDefault("name", ""));
        }
        if (!headers.isEmpty()) {
            builder.append(String.join("\t", headers)).append("\n");
        }
        for (List<String> row : result.getRows()) {
            List<String> values = new ArrayList<>();
            for (String cell : row) {
                values.add(nullToEmpty(cell).replace("\n", " "));
            }
            builder.append(String.join("\t", values)).append("\n");
        }
        return builder.toString().trim();
    }

    private void addRefsFromText(Set<String> candidates, String text) {
        if (text == null) {
            return;
        }
        Matcher matcher = SQL_TABLE_REF.matcher(text);
        while (matcher.find()) {
            addTableCandidate(candidates, matcher.group(1));
        }
    }

    @SuppressWarnings("unchecked")
    private void walkRequirementRefs(Set<String> candidates, Object value) {
        if (value instanceof Map) {
            Map<String, Object> map = (Map<String, Object>) value;
            for (Map.Entry<String, Object> entry : map.entrySet()) {
                if ("source_table".equals(entry.getKey()) || "table_en_name".equals(entry.getKey()) || "table_name".equals(entry.getKey())) {
                    addTableCandidate(candidates, entry.getValue());
                } else {
                    walkRequirementRefs(candidates, entry.getValue());
                }
            }
        } else if (value instanceof List) {
            for (Object item : (List<?>) value) {
                walkRequirementRefs(candidates, item);
            }
        }
    }

    @SuppressWarnings("unchecked")
    private void walkSourceRefs(Set<String> candidates, Object value) {
        if (value instanceof Map) {
            Map<String, Object> map = (Map<String, Object>) value;
            for (Map.Entry<String, Object> entry : map.entrySet()) {
                String key = entry.getKey();
                if ("source_table".equals(key) || "source_tables".equals(key)) {
                    addTableTokens(candidates, entry.getValue());
                } else if ("join_logic".equals(key) || "content_markdown".equals(key)) {
                    addRefsFromText(candidates, entry.getValue() == null ? "" : String.valueOf(entry.getValue()));
                    walkSourceRefs(candidates, entry.getValue());
                } else {
                    walkSourceRefs(candidates, entry.getValue());
                }
            }
        } else if (value instanceof List) {
            for (Object item : (List<?>) value) {
                walkSourceRefs(candidates, item);
            }
        }
    }

    private void addTableCandidate(Set<String> candidates, Object raw) {
        String text = raw == null ? "" : String.valueOf(raw).trim().replace("`", "").replace(";", "");
        text = text.replace("${bizdate}", "").trim();
        if (text.matches("[a-zA-Z_][\\w]*(?:\\.[a-zA-Z_][\\w]*)?")) {
            String lower = text.toLowerCase(Locale.ROOT);
            if (!SQL_KEYWORDS.contains(lower) && (text.contains(".") || text.length() >= 4)) {
                candidates.add(text);
            }
        }
    }

    private void addTableTokens(Set<String> candidates, Object raw) {
        String text = raw == null ? "" : String.valueOf(raw).replace("`", "").replace(";", " ");
        Matcher matcher = TABLE_IDENTIFIER.matcher(text);
        while (matcher.find()) {
            addTableCandidate(candidates, matcher.group());
        }
    }

    private String normalizeTableIdentifier(String tableRef) {
        String text = tableRef == null ? "" : tableRef.trim().replace("`", "");
        text = text.replaceAll(";+$", "");
        if (!text.matches("[a-zA-Z_][\\w]*(?:\\.[a-zA-Z_][\\w]*)?")) {
            throw new IllegalArgumentException("表名格式不合法: " + tableRef);
        }
        return text;
    }

    private List<Map<String, String>> rowsToDicts(DataphinQueryResult result) {
        List<String> columnNames = new ArrayList<>();
        for (Map<String, String> column : result.getColumns()) {
            columnNames.add(column.get("name"));
        }
        List<Map<String, String>> output = new ArrayList<>();
        for (List<String> row : result.getRows()) {
            Map<String, String> item = new LinkedHashMap<>();
            for (int i = 0; i < row.size() && i < columnNames.size(); i++) {
                item.put(columnNames.get(i), row.get(i));
            }
            output.add(item);
        }
        return output;
    }

    private void ensureCredentials() {
        if (isBlank(properties.getMaxcompute().getAccessKeyId()) || isBlank(properties.getMaxcompute().getAccessKeySecret())) {
            throw new IllegalStateException("MaxCompute 凭证未配置，请设置 ALIYUN_DATA_PLATFORM_AK / ALIYUN_DATA_PLATFORM_SK");
        }
    }

    private String normalizeProject(String projectName) {
        String project = isBlank(projectName) ? properties.getMaxcompute().getDefaultProject() : projectName.trim();
        if (!PROJECTS.contains(project)) {
            throw new IllegalArgumentException("不支持的 Dataphin 项目空间: " + project);
        }
        return project;
    }

    private int normalizeLimit(Integer limit, int defaultValue, int maximum) {
        int value = limit == null ? defaultValue : limit;
        if (value < 1) {
            return 1;
        }
        return Math.min(value, Math.max(1, maximum));
    }

    private String renderBizdate(String sql, String bizdate) {
        String resolved = isBlank(bizdate) ? defaultBizdate() : bizdate.trim();
        if (!resolved.matches("\\d{8}")) {
            throw new IllegalArgumentException("bizdate 必须为 yyyyMMdd 格式");
        }
        return (sql == null ? "" : sql).replace("${bizdate}", resolved);
    }

    private String defaultBizdate() {
        return LocalDate.now(ZoneId.of("Asia/Shanghai")).minusDays(1).format(DateTimeFormatter.BASIC_ISO_DATE);
    }

    private String ensureReadonlySql(String sql) {
        String text = sql == null ? "" : sql.trim();
        if (text.isEmpty()) {
            throw new IllegalArgumentException("SQL 不能为空");
        }
        String[] statements = text.split(";");
        int count = 0;
        String statement = "";
        for (String item : statements) {
            if (!item.trim().isEmpty()) {
                count++;
                statement = item.trim();
            }
        }
        if (count != 1) {
            throw new IllegalArgumentException("Dataphin 查询接口仅允许单条只读 SQL");
        }
        if (!READONLY.matcher(statement).find()) {
            throw new IllegalArgumentException("仅允许 SELECT/SHOW/DESC/DESCRIBE/EXPLAIN 查询");
        }
        if (FORBIDDEN.matcher(stripSqlLiteralsAndComments(statement)).find()) {
            throw new IllegalArgumentException("SQL 中包含非只读关键字，已拒绝执行");
        }
        return statement;
    }

    private String appendLimitIfNeeded(String sql, int limit) {
        if (!Pattern.compile("^\\s*select\\b", Pattern.CASE_INSENSITIVE).matcher(sql).find()) {
            return sql;
        }
        if (Pattern.compile("\\blimit\\s+\\d+\\s*$", Pattern.CASE_INSENSITIVE).matcher(sql).find()) {
            return sql;
        }
        return sql.replaceAll(";\\s*$", "") + " LIMIT " + limit;
    }

    private String stripSqlLiteralsAndComments(String sql) {
        String text = sql.replaceAll("(?s)/\\*.*?\\*/", " ");
        text = text.replaceAll("--[^\\n\\r]*", " ");
        text = text.replaceAll("'(?:''|[^'])*'", "''");
        text = text.replaceAll("\"(?:\"\"|[^\"])*\"", "\"\"");
        return text;
    }

    private String escapeSqlLiteral(String value) {
        return value == null ? "" : value.replace("'", "''");
    }

    private static String nullToEmpty(String value) {
        return value == null ? "" : value;
    }

    private static String joinNonBlank(String delimiter, String... items) {
        List<String> parts = new ArrayList<>();
        for (String item : items) {
            if (!isBlank(item)) {
                parts.add(item);
            }
        }
        return String.join(delimiter, parts);
    }

    private static boolean isBlank(String value) {
        return value == null || value.trim().isEmpty();
    }

    public static class SourceMetadata {
        private final List<String> tableRefs;
        private final String sourceSchema;
        private final String sourceSamples;

        public SourceMetadata(List<String> tableRefs, String sourceSchema, String sourceSamples) {
            this.tableRefs = tableRefs;
            this.sourceSchema = sourceSchema;
            this.sourceSamples = sourceSamples;
        }

        public List<String> getTableRefs() {
            return tableRefs;
        }

        public String getSourceSchema() {
            return sourceSchema;
        }

        public String getSourceSamples() {
            return sourceSamples;
        }
    }

    private static class SourceTableMetadata {
        private final String qualifiedName;
        private final String schemaText;
        private final String sampleText;

        private SourceTableMetadata(String qualifiedName, String schemaText, String sampleText) {
            this.qualifiedName = qualifiedName;
            this.schemaText = schemaText;
            this.sampleText = sampleText;
        }
    }
}
