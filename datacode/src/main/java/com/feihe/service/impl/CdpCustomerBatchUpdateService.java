package com.feihe.service.impl;

import com.aliyun.odps.data.Record;
import com.aliyun.odps.data.RecordReader;
import com.feihe.dto.CdpCustomerBatchUpdate;
import com.feihe.util.CdpQueryUtil;
import com.feihe.util.HttpUtil;
import com.feihe.util.JsonUtil;
import lombok.Data;
import lombok.experimental.Accessors;
import lombok.extern.slf4j.Slf4j;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.Callable;

@Slf4j
@SuppressWarnings("unchecked")
public class CdpCustomerBatchUpdateService implements Callable<Long> {
    private static final int BATCH_SIZE = 100;
    private final RecordReader recordReader;
    private final String workspaceId;
    private final String corporationId;
    private final String dataPlatformKeyId;
    private final String dataPlatformKey;
    private final String url;
    private final List<CdpCustomerBatchUpdateService.Param.Content> contentList = new ArrayList<>(
            BATCH_SIZE);


    public CdpCustomerBatchUpdateService(
            RecordReader recordReader,
            String doMain,
            String apiSuffix,
            String workspaceId,
            String corporationId,
            String dataPlatformKeyId,
            String dataPlatformKey) {
        this.url = doMain.concat(apiSuffix);
        this.recordReader = recordReader;
        this.workspaceId = workspaceId;
        this.corporationId = corporationId;
        this.dataPlatformKeyId = dataPlatformKeyId;
        this.dataPlatformKey = dataPlatformKey;

    }

    @Override
    public Long call() throws Exception {
        Long rowNum = 0L;
        Record r;
        while ((r = recordReader.read()) != null) {
            rowNum++;
            List<Map<String, String>> identity = (List<Map<String, String>>) r.get("identity");
            Map<String, Object> property = (Map<String, Object>) r.get("property");
            // 第一个身份放 CRM_ID
            Map<String, String> userIdentity = identity.remove(0);
            CdpCustomerBatchUpdateService.Param.Content content = new CdpCustomerBatchUpdateService.Param.Content()
                    .setUserIdentity(userIdentity)
                    // 使用CRM身份更新其他身份
                    .setIdentity(identity)
                    .setProperty(property);
            contentList.add(content);
            if (contentList.size() >= BATCH_SIZE) {
                rowNum = doSave(rowNum);
            }
        }
        if (!contentList.isEmpty()) {
            rowNum = doSave(rowNum);
        }
        return rowNum;
    }

    private Long doSave(Long rowNum) {
        CdpCustomerBatchUpdateService.Param param = new CdpCustomerBatchUpdateService.Param()
                .setContents(contentList);
        String payload = JsonUtil.toJson(param);
        try {
            String resp = HttpUtil.post(
                    url,
                    CdpQueryUtil.generatePathParam(this.workspaceId),
                    CdpQueryUtil.generateHeader(
                            this.corporationId,
                            this.dataPlatformKeyId,
                            this.dataPlatformKey),
                    payload);
            CdpCustomerBatchUpdate.Response response = JsonUtil.fromJson(
                    resp,
                    CdpCustomerBatchUpdate.Response.class);
            if (Objects.nonNull(response)
                    && response.getCode().equals(0)
                    && response.getData().getFailRecords().isEmpty()) {
                log.info("update entity data on cdp success, process {} record", rowNum);
            } else {
                log.error(
                        "update entity data on cdp error, request:{}, url:{}, response:{}",
                        payload,
                        url,
                        response);
                rowNum -= Objects.requireNonNull(response).getData().getFailRecords().size();
                log.info("update entity data on cdp success, process {} record", rowNum);
            }
        } catch (Exception e) {
            log.error(
                    "update entity data on cdp error:{}, url:{}, payload:{}",
                    e.getMessage(),
                    url,
                    payload,
                    e);
            rowNum -= contentList.size();
        } finally {
            contentList.clear();
        }
        return rowNum;
    }

    @Data
    @Accessors
    public static class Param {
        // 1 <= contents.size() <= 100
        private List<Content> contents;

        @Data
        public static class Content {
            // 客户社交账号用于更新数据 只取1个就可以, 使用业务member_id CRMxx
            private Map<String, String> userIdentity;
            private List<Map<String, String>> identity;
            // 客户属性
            private Map<String, Object> property;
        }
    }
}
