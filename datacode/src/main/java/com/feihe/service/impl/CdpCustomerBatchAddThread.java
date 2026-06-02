package com.feihe.service.impl;

import com.aliyun.odps.TableSchema;
import com.aliyun.odps.data.Record;
import com.aliyun.odps.data.RecordReader;
import com.feihe.annotation.Identity;
import com.feihe.common.CdpSystemConstant;
import com.feihe.dto.CdpCustomerBatchAdd;
import com.feihe.enumerate.CustomerIdentityType;
import com.feihe.util.CdpQueryUtil;
import com.feihe.util.HttpUtil;
import com.feihe.util.JsonUtil;
import com.feihe.util.OdpsUtil;
import lombok.extern.slf4j.Slf4j;

import java.io.IOException;
import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.Callable;

/**
 * <a href="https://tmc.qidian.qq.com/base/console/doc/14615?version=20260203">客户批量创建</a>
 */
@Slf4j
@SuppressWarnings("java:S3011")
public class CdpCustomerBatchAddThread<T> implements Callable<Long> {
    private static final String API_SUFFIX = "/cdp-entity/user/batchCreate";
    private static final int BATCH_SIZE = 100;
    private final List<T> dataList = new ArrayList<>(BATCH_SIZE);

    private final RecordReader recordReader;
    private final TableSchema tableSchema;
    private final Class<T> clazz;
    private final String workspaceId;
    private final String dataPlatformKeyId;
    private final String dataPlatformKey;
    private final String corporationId;

    public CdpCustomerBatchAddThread(
            RecordReader recordReader,
            TableSchema tableSchema,
            Class<T> clazz,
            String workspaceId,
            String corporationId,
            String dataPlatformKeyId,
            String dataPlatformKey) {
        this.recordReader = recordReader;
        this.tableSchema = tableSchema;
        this.clazz = clazz;
        this.workspaceId = workspaceId;
        this.dataPlatformKeyId = dataPlatformKeyId;
        this.corporationId = corporationId;
        this.dataPlatformKey = dataPlatformKey;
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
                    doSave(JsonUtil.convertToMapList(dataList, false), clazz);
                    dataList.clear();
                }
            }
            if (!dataList.isEmpty()) {
                doSave(JsonUtil.convertToMapList(dataList, false), clazz);
                dataList.clear();
            }
            recordReader.close();
        } catch (IOException e) {
            log.error("read record error:{}", e.getMessage(), e);
        }
        return recordNum;
    }


    public void doSave(List<Map<String, String>> sourceData, Class<?> clazz) {
        CustomerIdentityType identityType = CustomerIdentityType.MEMBER_ID;
        Field[] declaredFields = clazz.getDeclaredFields();
        for (Field field : declaredFields) {
            field.setAccessible(true);
            if (field.isAnnotationPresent(Identity.class)) {
                Identity identity = field.getAnnotation(Identity.class);
                identityType = identity.value();
            }
        }
        List<CdpCustomerBatchAdd.Param.Content> contents = new ArrayList<>();
        CustomerIdentityType finalIdentityType = identityType;
        sourceData
                .forEach(entry -> {
                    CdpCustomerBatchAdd.Param.Content.IdentityValue customerIdentity = new CdpCustomerBatchAdd.Param.Content.IdentityValue()
                            .setIdentityType(finalIdentityType.getType())
                            .setIdentityValue(entry.get(finalIdentityType.getFieldKey()));
                    entry.remove(finalIdentityType.getFieldKey());
                    CdpCustomerBatchAdd.Param.Content content = new CdpCustomerBatchAdd.Param.Content()
                            .setIdentity(Collections.singletonList(customerIdentity))
                            .setProperty(new HashMap<>(entry));
                    contents.add(content);
                });

        CdpCustomerBatchAdd.Param cdpCustomerBatchAddParam = new CdpCustomerBatchAdd.Param()
                .setContents(contents);
        String url = CdpSystemConstant
                .getDoMain()
                .concat(CdpSystemConstant.API_PREFIX)
                .concat(CdpCustomerBatchAddThread.API_SUFFIX);
        String payload = JsonUtil.toJson(cdpCustomerBatchAddParam);
        try {
            String resp = HttpUtil.post(
                    url,
                    CdpQueryUtil.generatePathParam(this.workspaceId),
                    CdpQueryUtil.generateHeader(
                            this.corporationId,
                            this.dataPlatformKeyId,
                            this.dataPlatformKey),
                    payload);
            CdpCustomerBatchAdd.Response response = JsonUtil.fromJson(
                    resp,
                    CdpCustomerBatchAdd.Response.class);
            if (Objects.nonNull(response)
                    && response.getCode().equals(0)
                    && response.getData().getFailCount() <= 0) {
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
