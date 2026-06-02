package com.feihe.datacode.service;

import com.feihe.datacode.model.ValidationResult;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

@Service
public class SqlValidatorService {
    private static final Pattern DISALLOWED = Pattern.compile("\\b(drop|truncate|delete|update|grant|revoke|create\\s+database)\\b", Pattern.CASE_INSENSITIVE);
    private static final Pattern INSERT = Pattern.compile("\\binsert\\s+(overwrite|into)\\s+table\\b", Pattern.CASE_INSENSITIVE);
    private static final Pattern CREATE_TABLE = Pattern.compile("\\bcreate\\s+(external\\s+)?table\\b", Pattern.CASE_INSENSITIVE);
    private static final Pattern SQL_FENCE = Pattern.compile("```(?:sql)?\\s*([\\s\\S]*?)```", Pattern.CASE_INSENSITIVE);

    public String extractSql(String text) {
        String content = text == null ? "" : text.trim();
        Matcher matcher = SQL_FENCE.matcher(content);
        if (matcher.find()) {
            return matcher.group(1).trim();
        }
        return content;
    }

    public ValidationResult validateDataphinSql(String text) {
        String sql = extractSql(text);
        String compact = stripComments(sql);
        String lower = compact.toLowerCase(Locale.ROOT);
        List<String> errors = new ArrayList<>();
        List<String> warnings = new ArrayList<>();

        if (sql.trim().isEmpty()) {
            errors.add("未生成 SQL");
            return new ValidationResult(false, errors, warnings, sql);
        }
        if (DISALLOWED.matcher(compact).find()) {
            errors.add("SQL 中包含高风险或非开发写入语句（DROP/TRUNCATE/DELETE/UPDATE/GRANT/REVOKE 等）");
        }
        if (!CREATE_TABLE.matcher(compact).find()) {
            errors.add("缺少 CREATE TABLE 建表语句");
        }
        if (!INSERT.matcher(compact).find()) {
            errors.add("缺少 INSERT OVERWRITE/INSERT INTO TABLE 写入语句");
        }
        if (!lower.contains("partition") || !Pattern.compile("\\bds\\b", Pattern.CASE_INSENSITIVE).matcher(compact).find()) {
            errors.add("缺少默认分区设计，必须包含 ds 分区字段");
        }
        if (!sql.contains("${bizdate}")) {
            errors.add("缺少默认业务日期变量 ${bizdate}");
        }
        if (Pattern.compile("\\bselect\\s+\\*", Pattern.CASE_INSENSITIVE).matcher(compact).find()) {
            warnings.add("建议不要 SELECT *，应显式列出目标字段");
        }
        if (!balancedQuotes(sql)) {
            errors.add("引号不成对");
        }
        if (!balancedParentheses(sql)) {
            errors.add("括号不成对");
        }
        if (lower.contains("insert overwrite")
                && !Pattern.compile("partition\\s*\\(\\s*ds\\s*=\\s*['\\\"]?\\$\\{bizdate}['\\\"]?\\s*\\)", Pattern.CASE_INSENSITIVE).matcher(compact).find()) {
            warnings.add("建议 INSERT OVERWRITE TABLE 使用 PARTITION (ds='${bizdate}') 显式写分区");
        }
        if (!Pattern.compile("\\bwhere\\b[\\s\\S]{0,800}\\bds\\s*=\\s*['\\\"]?\\$\\{bizdate}['\\\"]?", Pattern.CASE_INSENSITIVE).matcher(compact).find()) {
            warnings.add("建议源表查询按 ds='${bizdate}' 做默认分区过滤");
        }
        if (!sql.trim().endsWith(";")) {
            warnings.add("建议 SQL 语句以分号结尾，方便一键复制执行");
        }
        if (Pattern.compile("^\\s*(解释|说明|以下|根据)", Pattern.CASE_INSENSITIVE).matcher(sql).find()) {
            errors.add("模型返回中包含解释性正文，请只保留 SQL");
        }
        return new ValidationResult(errors.isEmpty(), errors, warnings, sql);
    }

    private String stripComments(String sql) {
        String text = sql == null ? "" : sql;
        text = text.replaceAll("(?s)/\\*.*?\\*/", " ");
        text = text.replaceAll("--[^\\n\\r]*", " ");
        return text;
    }

    private boolean balancedQuotes(String sql) {
        boolean single = false;
        boolean dbl = false;
        boolean escaped = false;
        for (int i = 0; i < sql.length(); i++) {
            char ch = sql.charAt(i);
            if (escaped) {
                escaped = false;
                continue;
            }
            if (ch == '\\') {
                escaped = true;
                continue;
            }
            if (ch == '\'' && !dbl) {
                single = !single;
            } else if (ch == '"' && !single) {
                dbl = !dbl;
            }
        }
        return !single && !dbl;
    }

    private boolean balancedParentheses(String sql) {
        int depth = 0;
        boolean single = false;
        boolean dbl = false;
        for (int i = 0; i < sql.length(); i++) {
            char ch = sql.charAt(i);
            if (ch == '\'' && !dbl) {
                single = !single;
                continue;
            }
            if (ch == '"' && !single) {
                dbl = !dbl;
                continue;
            }
            if (single || dbl) {
                continue;
            }
            if (ch == '(') {
                depth++;
            } else if (ch == ')') {
                depth--;
                if (depth < 0) {
                    return false;
                }
            }
        }
        return depth == 0;
    }
}
