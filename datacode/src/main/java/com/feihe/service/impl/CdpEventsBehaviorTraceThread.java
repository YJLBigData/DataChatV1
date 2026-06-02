package com.feihe.service.impl;

import com.aliyun.odps.TableSchema;
import com.aliyun.odps.data.Record;
import com.aliyun.odps.data.RecordReader;
import com.feihe.annotation.Account;
import com.feihe.annotation.EventCode;
import com.feihe.annotation.EventTime;
import com.feihe.common.CdpSystemConstant;
import com.feihe.dto.CdpEventsBehaviorLog;
import com.feihe.exception.RdoReflectException;
import com.feihe.util.DateUtil;
import com.feihe.util.HttpUtil;
import com.feihe.util.JsonUtil;
import com.feihe.util.OdpsUtil;
import lombok.extern.slf4j.Slf4j;

import java.io.IOException;
import java.lang.reflect.Field;
import java.lang.reflect.Modifier;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.Callable;

/**
 * <a href="https://tmc.qidian.qq.com/base/console/doc/14604?version=20260203">服务端历史行为数据上报</a>
 */
@Slf4j
@SuppressWarnings("java:S3011")
public class CdpEventsBehaviorTraceThread<T> implements Callable<Long> {
    private static final String API_SUFFIX = "/events/history/trace";
    private static final Integer BATCH_SIZE = 100;
    private final List<T> dataList = new ArrayList<>(BATCH_SIZE);
    private final RecordReader recordReader;
    private final TableSchema tableSchema;
    private final Class<T> clazz;
    private final String appKey;


    public CdpEventsBehaviorTraceThread(
            RecordReader recordReader,
            TableSchema tableSchema,
            Class<T> clazz) {
        this.recordReader = recordReader;
        this.tableSchema = tableSchema;
        this.clazz = clazz;
        try {
            this.appKey = clazz.getDeclaredField("APP_KEY").get(null).toString();
        } catch (Exception e) {
            throw new RdoReflectException("For event behavior app_key mast be set", e);
        }
    }

    @Override
    public Long call() {
        Long recordNum = 0L;
        try {
            Record row;
            while ((row = recordReader.read()) != null) {
                recordNum++;
                T t = OdpsUtil.convertRecordToEntity(clazz, row, tableSchema);
                dataList.add(t);
                if (dataList.size() >= BATCH_SIZE) {
                    doSave(transform(this.dataList), appKey);
                    dataList.clear();
                }
            }
            if (!dataList.isEmpty()) {
                doSave(transform(this.dataList), appKey);
                dataList.clear();
            }
            recordReader.close();
        } catch (IOException e) {
            log.error("read record error:{}", e.getMessage(), e);
        }
        return recordNum;
    }


    private static final Map<Class<?>, CdpEventsBehaviorTraceThread.FieldCategory> fieldCache = new HashMap<>();

    private static class FieldCategory {
        Field timeField;
        Field eventField;
        List<Field> accountFields = new ArrayList<>();
        List<Field> propertyFields = new ArrayList<>();
    }

    /**
     * 主转换方法
     */
    private static <T> List<CdpEventsBehaviorLog.Param.EventsData> transform(List<T> dataList) {
        if (dataList == null || dataList.isEmpty()) {
            return Collections.emptyList();
        }
        // 获取或缓存字段分类
        Class<?> clazz = dataList.get(0).getClass();
        CdpEventsBehaviorTraceThread.FieldCategory category = fieldCache.computeIfAbsent(
                clazz,
                CdpEventsBehaviorTraceThread::analyzeFields);
        if (null == category.timeField || null == category.eventField
                || category.accountFields.isEmpty()) {
            throw new RdoReflectException("time, event, account mast set");
        }
        List<CdpEventsBehaviorLog.Param.EventsData> result = new ArrayList<>();
        for (T data : dataList) {
            // beanMap: true 驼峰, false snake
            CdpEventsBehaviorLog.Param.EventsData eventData = convertSingle(data, category, false);
            result.add(eventData);
        }
        return result;
    }

    /**
     * 分析字段分类
     */
    private static CdpEventsBehaviorTraceThread.FieldCategory analyzeFields(Class<?> clazz) {
        CdpEventsBehaviorTraceThread.FieldCategory category = new CdpEventsBehaviorTraceThread.FieldCategory();
        for (Field field : clazz.getDeclaredFields()) {
            field.setAccessible(true);
            // 静态字段用来标记appKey
            if (Modifier.isStatic(field.getModifiers())) {
                continue;
            }
            // 优先级：Event > Account > 其他
            if (field.isAnnotationPresent(EventCode.class)) {
                category.eventField = field;
            } else if (field.isAnnotationPresent(EventTime.class)) {
                category.timeField = field;
            } else if (field.isAnnotationPresent(Account.class)) {
                category.accountFields.add(field);
            } else {
                category.propertyFields.add(field);
            }
        }
        return category;
    }

    private static <T> CdpEventsBehaviorLog.Param.EventsData convertSingle(
            T data,
            CdpEventsBehaviorTraceThread.FieldCategory category, boolean beanMapping) {
        CdpEventsBehaviorLog.Param.EventsData result = new CdpEventsBehaviorLog.Param.EventsData();

        try {
            // 1. 设置 time
            if (null != category.timeField) {
                if (category.timeField.getType() != LocalDateTime.class) {
                    throw new RdoReflectException("time field type error");
                }
                LocalDateTime timeValue = (LocalDateTime) category.timeField.get(data);
                result.setTime(null != timeValue ? DateUtil.toEpochMilli(timeValue) : null);
            }
            // 2. 设置 event
            if (null != category.eventField) {
                Object eventValue = category.eventField.get(data);
                result.setEvent(null != eventValue ? eventValue.toString() : null);

            }

            // 3. 填充账号信息
            Map<String, Object> accountMap = new HashMap<>();
            for (Field field : category.accountFields) {
                Object value = field.get(data);
                if (value != null) {
                    String name = field.getName();
                    if (!beanMapping) {
                        name = OdpsUtil.camelToSnake(name);
                    }
                    accountMap.put(name, value);
                }
            }
            result.setAccount(accountMap);
            // 4. 填充其他属性
            Map<String, Object> propMap = new HashMap<>();
            for (Field field : category.propertyFields) {
                Object value = field.get(data);
                if (null != value) {
                    String name = field.getName();
                    if (!beanMapping) {
                        name = OdpsUtil.camelToSnake(name);
                    }
                    propMap.put(name, value);
                }
            }
            result.setProperties(propMap);
        } catch (IllegalAccessException e) {
            throw new RdoReflectException("field access failure", e);
        }
        return result;
    }

    public void doSave(
            List<CdpEventsBehaviorLog.Param.EventsData> sourceData,
            String appKey) {
        String dataJson = JsonUtil.toJson(sourceData);
        String url = CdpSystemConstant
                .getLogDomain()
                .concat(CdpEventsBehaviorTraceThread.API_SUFFIX);
        String payload = JsonUtil.toJson(new CdpEventsBehaviorLog.Param(
                appKey,
                Objects.requireNonNull(dataJson),
                3));
        try {
            String resp = HttpUtil.post(
                    url,
                    null,
                    null,
                    payload);
            CdpEventsBehaviorLog.Response response = JsonUtil.fromJson(
                    resp,
                    CdpEventsBehaviorLog.Response.class);

            if (Objects.nonNull(response) && response.getCode().equals(0)) {
                log.info("send entity data to cdp success");
            } else {
                log.error(
                        "send entity data to cdp error:{}, url:{}, payload:{}",
                        resp,
                        url,
                        payload);
            }
        } catch (IOException e) {
            log.error(
                    "send entity data to cdp error:{}, url:{}, payload:{}",
                    e.getMessage(),
                    url,
                    payload,
                    e);
        }
    }
}
