package com.feihe.datacode.service;

import com.feihe.datacode.config.DataCodeProperties;
import org.springframework.stereotype.Component;

import javax.annotation.PostConstruct;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.SQLException;
import java.sql.Statement;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;

@Component
public class SqliteStore {
    private final DataCodeProperties properties;
    private Path databasePath;

    public SqliteStore(DataCodeProperties properties) {
        this.properties = properties;
    }

    @PostConstruct
    public void init() throws Exception {
        Class.forName("org.sqlite.JDBC");
        databasePath = Paths.get(properties.getDatabasePath()).toAbsolutePath().normalize();
        if (databasePath.getParent() != null) {
            Files.createDirectories(databasePath.getParent());
        }
        try (Connection conn = connect(); Statement statement = conn.createStatement()) {
            statement.execute("PRAGMA journal_mode=WAL");
            statement.execute("CREATE TABLE IF NOT EXISTS users ("
                    + "user_id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    + "username TEXT NOT NULL UNIQUE,"
                    + "display_name TEXT NOT NULL,"
                    + "password_hash TEXT NOT NULL,"
                    + "role TEXT NOT NULL,"
                    + "must_change_password INTEGER NOT NULL DEFAULT 1,"
                    + "is_active INTEGER NOT NULL DEFAULT 1,"
                    + "created_at TEXT NOT NULL,"
                    + "last_login TEXT"
                    + ")");
            statement.execute("CREATE TABLE IF NOT EXISTS admin_audit_log ("
                    + "log_id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    + "operator_user_id INTEGER NOT NULL,"
                    + "action TEXT NOT NULL,"
                    + "target_user_id INTEGER,"
                    + "detail TEXT NOT NULL DEFAULT '{}',"
                    + "trace_id TEXT,"
                    + "request_id TEXT,"
                    + "created_at TEXT NOT NULL"
                    + ")");
            statement.execute("CREATE INDEX IF NOT EXISTS idx_admin_audit_created_at ON admin_audit_log(created_at)");
            statement.execute("CREATE TABLE IF NOT EXISTS generation_log ("
                    + "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    + "trace_id TEXT NOT NULL UNIQUE,"
                    + "user_id TEXT NOT NULL,"
                    + "raw_input TEXT,"
                    + "metric TEXT,"
                    + "status TEXT NOT NULL DEFAULT 'pending',"
                    + "elapsed_ms REAL,"
                    + "created_at TEXT NOT NULL,"
                    + "updated_at TEXT NOT NULL,"
                    + "provider_id TEXT,"
                    + "model_name TEXT,"
                    + "error_msg TEXT,"
                    + "prompt_text TEXT,"
                    + "source_schema TEXT,"
                    + "source_samples TEXT,"
                    + "notes TEXT,"
                    + "requirements_json TEXT,"
                    + "generated_sql TEXT,"
                    + "validation_json TEXT,"
                    + "trace_meta_json TEXT"
                    + ")");
            statement.execute("CREATE INDEX IF NOT EXISTS idx_generation_log_user ON generation_log(user_id)");
            statement.execute("CREATE INDEX IF NOT EXISTS idx_generation_log_status ON generation_log(status)");
            statement.execute("CREATE INDEX IF NOT EXISTS idx_generation_log_created ON generation_log(created_at)");
            statement.execute("CREATE TABLE IF NOT EXISTS llm_invocation_log ("
                    + "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    + "trace_id TEXT,"
                    + "request_id TEXT,"
                    + "round_no INTEGER,"
                    + "stage TEXT NOT NULL,"
                    + "provider_id TEXT NOT NULL,"
                    + "model_name TEXT NOT NULL,"
                    + "request_json TEXT NOT NULL,"
                    + "response_json TEXT,"
                    + "error_message TEXT,"
                    + "created_at TEXT NOT NULL"
                    + ")");
            statement.execute("CREATE INDEX IF NOT EXISTS idx_llm_invocation_trace ON llm_invocation_log(trace_id, created_at)");
            statement.execute("CREATE TABLE IF NOT EXISTS system_setting ("
                    + "setting_key TEXT PRIMARY KEY,"
                    + "setting_value TEXT NOT NULL,"
                    + "updated_at TEXT NOT NULL,"
                    + "updated_by TEXT"
                    + ")");
        }
    }

    public Connection connect() throws SQLException {
        Connection conn = DriverManager.getConnection("jdbc:sqlite:" + databasePath);
        try (Statement statement = conn.createStatement()) {
            statement.execute("PRAGMA foreign_keys=ON");
            statement.execute("PRAGMA busy_timeout=30000");
        }
        return conn;
    }

    public static String utcNow() {
        return OffsetDateTime.now(ZoneOffset.UTC).toString();
    }
}
