package com.feihe.datacode.model;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public class DataphinQueryResult {
    private String sql;
    private String project;
    private List<Map<String, String>> columns = new ArrayList<>();
    private List<List<String>> rows = new ArrayList<>();
    private int rowCount;
    private boolean hasResultSet = true;

    public Map<String, Object> toPayload() {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("ok", true);
        payload.put("project", project);
        payload.put("columns", columns);
        payload.put("rows", rows);
        payload.put("row_count", rowCount);
        payload.put("has_result_set", hasResultSet);
        payload.put("executed_sql", sql);
        payload.put("requested_sql", sql);
        return payload;
    }

    public String getSql() {
        return sql;
    }

    public void setSql(String sql) {
        this.sql = sql;
    }

    public String getProject() {
        return project;
    }

    public void setProject(String project) {
        this.project = project;
    }

    public List<Map<String, String>> getColumns() {
        return columns;
    }

    public void setColumns(List<Map<String, String>> columns) {
        this.columns = columns;
    }

    public List<List<String>> getRows() {
        return rows;
    }

    public void setRows(List<List<String>> rows) {
        this.rows = rows;
    }

    public int getRowCount() {
        return rowCount;
    }

    public void setRowCount(int rowCount) {
        this.rowCount = rowCount;
    }

    public boolean isHasResultSet() {
        return hasResultSet;
    }

    public void setHasResultSet(boolean hasResultSet) {
        this.hasResultSet = hasResultSet;
    }
}
