package com.feihe.util;

import com.aliyun.odps.Column;
import com.aliyun.odps.Instance;
import com.aliyun.odps.Odps;
import com.aliyun.odps.PartitionSpec;
import com.aliyun.odps.TableSchema;
import com.aliyun.odps.account.Account;
import com.aliyun.odps.account.AliyunAccount;
import com.aliyun.odps.data.Record;
import com.aliyun.odps.data.RecordWriter;
import com.aliyun.odps.task.SQLTask;
import com.aliyun.odps.tunnel.TableTunnel;
import com.aliyun.odps.tunnel.TunnelException;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.feihe.FeiHeCdpApplication;
import com.feihe.annotation.Derive;
import com.feihe.annotation.OdpsTable;
import com.feihe.common.OdpsConstant;
import com.google.common.base.CaseFormat;
import lombok.extern.slf4j.Slf4j;

import java.lang.reflect.Field;
import java.lang.reflect.Modifier;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.Statement;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

@Slf4j
public class OdpsUtil {
    private OdpsUtil() {
    }

    private static final Odps odps;
    private static final TableTunnel tunnel;

    static {
        Account account = new AliyunAccount(FeiHeCdpApplication.ak,
                FeiHeCdpApplication.sk
                );
        odps = new Odps(account);
        odps.setEndpoint(OdpsConstant.END_POINT);
        odps.setDefaultProject(OdpsConstant.DEFAULT_PROJECT);
        tunnel = new TableTunnel(odps);
        tunnel.setEndpoint(OdpsConstant.TUNNEL_SERVER);
    }

    public static <T> List<T> getDataBean(String sql, Class<T> clazz) {
        return convertToEntityList(getData(sql), clazz);
    }

    @SuppressWarnings("java:S3011")
    public static <T> T convertRecordToEntity(
            Class<T> clazz,
            Record row,
            TableSchema tableSchema) {
        T object = null;
        try {
            object = clazz.getDeclaredConstructor().newInstance();
            Field[] declaredFields = clazz.getDeclaredFields();
            for (Field declaredField : declaredFields) {
                declaredField.setAccessible(true);
                if (Modifier.isStatic(declaredField.getModifiers())) {
                    continue;
                }
                String fieldName;
                if (declaredField.isAnnotationPresent(Derive.class)) {
                    fieldName = declaredField.getAnnotation(Derive.class).value();
                } else if (declaredField.isAnnotationPresent(JsonProperty.class)) {
                    fieldName = declaredField.getAnnotation(JsonProperty.class).value();
                } else {
                    fieldName = declaredField.getName();
                }
                String columnName = camelToSnake(fieldName);
                Column column = tableSchema.getColumn(columnName);
                // string   datetime  bigint  double  decimal
                switch (column.getTypeInfo().getOdpsType()) {
                    case BIGINT:
                        declaredField.set(object, row.getBigint(columnName));
                        break;
                    case DOUBLE:
                        declaredField.set(object, row.getDouble(columnName));
                        break;
                    case DECIMAL:
                        declaredField.set(object, row.getDecimal(columnName));
                        break;
                    case DATETIME:
                        declaredField.set(
                                object,
                                LocalDateTime.ofInstant(
                                        row.getDatetime(columnName).toInstant(),
                                        ZoneId.systemDefault()));
                        break;
                    default:
                        declaredField.set(object, row.getString(columnName));
                }
            }
        } catch (Exception e) {
            log.error("error when convert record to entity: {}", e.getMessage(), e);
            return null;
        }
        return object;
    }


    public static List<Map<String, String>> getData(String sql) {
        log.info("begin get data, sql: {}", sql);
        try {
            List<Map<String, String>> recordList = new ArrayList<>();
            Instance i = SQLTask.run(odps, sql);
            i.waitForSuccess();
            log.info("query data platform");
            List<Record> records = SQLTask.getResult(i);
            if (null == records || records.isEmpty()) {
                return Collections.emptyList();
            }
            for (Record r : records) {
                Map<String, String> singleRecord = new HashMap<>();
                int columnCount = r.getColumnCount();
                for (int s = 0; s < columnCount; s++) {
                    String columnName = r.getColumns()[s].getName();
                    String columnData = null;
                    if (Objects.nonNull(r.get(s))) {
                        columnData = r.get(s).toString();
                    }
                    singleRecord.put(columnName, columnData);
                }
                recordList.add(singleRecord);
            }
            return recordList;
        } catch (Exception e) {
            log.error("sql error: {}", sql, e);
        }
        return Collections.emptyList();
    }

    public static void saveData(
            List<List<String>> list,
            String tableName,
            String projectName,
            String bizDate) throws Exception {
        TableTunnel.UploadSession uploadSession;
        String partition = PartitionUtil.bizDateEq(bizDate);
        PartitionSpec partitionSpec = new PartitionSpec(partition);
        // 在服务端的本表本分区上创建一个有效期为24小时的Session。
        // 24小时内，该Session共计可以上传20000个分区（Block）数据。
        // 创建Session的耗时在秒级，且需要在服务端使用部分资源、创建临时目录等，需要消耗较多的资源。
        // 建议同一个分区的数据尽可能通过复用Session的方式上传。

        uploadSession = tunnel.createUploadSession(projectName, tableName, partitionSpec);

        // 数据准备完成后，打开Writer开始写入数据，将数据写入一个Block。
        // 每个Block仅能成功上传一次，不可重复上传。CloseWriter执行成功即代表该Block上传成功，如果失败可重新上传该Block。
        // 同一个Session最多可以包含20000个BlockId（即0-19999）。如果超出20000个需要执行Commit Session并重新创新一个新的Session。
        // 单个Block内如果写入的数据过少将产生大量小文件，严重影响计算性能。强烈建议每次写入64 MB以上的数据（同一Block支持写入100 GB以内的数据）。
        // 如果创建一个Session后仅上传少量数据，不仅会造成小文件、空目录等问题，还会严重影响上传性能（创建Session耗时数秒，而真正数据上传可能仅花费十几毫秒）。
        // Writer创建后任意一段时间内，如果任意连续两分钟内没有写入4 KB以上的数据，将会超时并自动断开连接。
        // 建议在创建Writer前，在内存中准备好可以直接写入的数据。
        // 生成TunnelBufferedWriter的实例。
        // 打开一个记录写入器，编号为 0（表示第一个数据块）

        try (RecordWriter recordWriter = uploadSession.openRecordWriter(0)) {
            Record arrayRecord = uploadSession.newRecord();
            for (List<String> rowData : list) {
                for (int i = 0; i < rowData.size(); i++) {
                    arrayRecord.set(i, rowData.get(i));
                }
                // 调用write接口写入数据。
                recordWriter.write(arrayRecord);
            }
            // uploadSession提交，结束上传。
        } catch (Exception e) {
            log.error("data save field: {}", e.getMessage(), e);
        }
        uploadSession.commit();
    }


    @SuppressWarnings("java:S6437")
    public static void dropCreatePartition(
            String tableName,
            String projectName,
            String bizDate) throws Exception {
        Connection conn = null;
        Statement stmt = null;
        try {
            Class.forName(OdpsConstant.DRIVER_NAME);
            conn = DriverManager.getConnection(
                    OdpsConstant.ODPS_URL,
                    OdpsConstant.aliYunDataPlatformAk(),
                    OdpsConstant.aliYunDataPlatformSk());
            stmt = conn.createStatement();
            log.info("delete {}.{} ds={}", projectName, tableName, bizDate);
            String dropPartitionSql = "alter table" + " "
                    + projectName + "." + tableName + " "
                    + "drop if exists partition(" + PartitionUtil.sqlBizDate(bizDate) + ")";
            stmt.execute(dropPartitionSql);
            log.info(
                    "{}.{} ds={} delete success, sql={}",
                    projectName,
                    tableName,
                    bizDate,
                    dropPartitionSql);
            log.info("create {}.{} ds={}", projectName, tableName, bizDate);
            String cratePartitionSql = "alter table" + " "
                    + projectName + "." + tableName + " "
                    + "add if not exists partition(" + PartitionUtil.sqlBizDate(bizDate) + ")";
            stmt.execute(cratePartitionSql);
            log.info(
                    "{}.{} ds={} process success, sql={}",
                    projectName,
                    tableName,
                    bizDate,
                    cratePartitionSql);
        } catch (Exception e) {
            log.error("drop create partition error :{}", e.getMessage(), e);
            throw e;
        } finally {
            if (stmt != null) {
                try {
                    stmt.close();
                } catch (Exception e) {
                    // ignore
                }
            }
            if (conn != null) {
                try {
                    conn.close();
                } catch (Exception e) {
                    // ignore
                }
            }
        }
    }

    public static String camelToSnake(String camelCase) {
        return CaseFormat.LOWER_CAMEL.to(CaseFormat.LOWER_UNDERSCORE, camelCase);
    }

    public static <T> List<T> convertToEntityList(
            List<Map<String, String>> dataList,
            Class<T> clazz) {
        List<T> resultList = new ArrayList<>();
        if (null == dataList || dataList.isEmpty()) {
            return resultList;
        }

        for (Map<String, String> data : dataList) {
            T entity = convertToEntity(data, clazz);
            resultList.add(entity);
        }
        return resultList;
    }

    @SuppressWarnings("java:S3011")
    public static <T> T convertToEntity(Map<String, String> data, Class<T> clazz) {
        try {
            T entity = clazz.getDeclaredConstructor().newInstance();
            Field[] fields = clazz.getDeclaredFields();

            for (Field field : fields) {
                String fieldName;

                if (field.isAnnotationPresent(Derive.class)) {
                    fieldName = field.getAnnotation(Derive.class).value();
                } else {
                    fieldName = field.getName();
                }

                // 将驼峰转为下划线格式去匹配 map 中的 key
                String underscoreName = camelToSnake(fieldName);

                // 优先使用下划线格式的 key 查找，如果找不到再尝试驼峰格式
                String value = data.get(underscoreName);
                if (Objects.isNull(value)) {
                    value = data.get(fieldName);
                }
                if (Objects.nonNull(value)) {

                    field.setAccessible(true);
                    // 根据字段类型进行类型转换
                    field.set(entity, value);
                }
            }
            return entity;
        } catch (Exception e) {
            log.error("convert entity error: {}", e.getMessage(), e);
            return null;
        }
    }


    public static <T> TableTunnel.DownloadSession tunnelDownloadSession(Class<T> clazz) throws TunnelException {
        return tunnelDownloadSession(clazz, PartitionUtil.bizDate());
    }

    public static TableTunnel.DownloadSession tunnelDownloadSession(
            String project,
            String table) throws TunnelException {
        return tunnelDownloadSession(project, table, PartitionUtil.bizDate());
    }

    public static <T> TableTunnel.DownloadSession tunnelDownloadSession(
            Class<T> clazz,
            String partition) throws TunnelException {
        if (clazz.isAnnotationPresent(OdpsTable.class)) {
            throw new TunnelException("odps project table is required");
        }
        OdpsTable odpsTable = clazz.getAnnotation(OdpsTable.class);
        String project = odpsTable.project();
        String table = odpsTable.table();
        PartitionSpec partitionSpec = new PartitionSpec(partition);
        return tunnel.createDownloadSession(project, table, partitionSpec, true);
    }

    public static TableTunnel.DownloadSession tunnelDownloadSession(
            String project, String table,
            String partition) throws TunnelException {
        PartitionSpec partitionSpec = new PartitionSpec(partition);
        return tunnel.createDownloadSession(project, table, partitionSpec, true);
    }
}
