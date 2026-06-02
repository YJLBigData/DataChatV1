package com.feihe.service.impl;

import com.aliyun.odps.data.Record;
import com.aliyun.odps.data.RecordReader;
import com.feihe.dto.CdpCustomerBatchAdd;
import com.feihe.util.CdpQueryUtil;
import com.feihe.util.HttpUtil;
import com.feihe.util.JsonUtil;
import com.feihe.util.OdpsUtil;
import com.feihe.util.PartitionUtil;
import lombok.Data;
import lombok.experimental.Accessors;
import lombok.extern.slf4j.Slf4j;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.Callable;
import java.util.concurrent.atomic.LongAdder;

@Slf4j
@SuppressWarnings("unchecked")
public class CdpCustomerBatchAddService implements Callable<Long> {
    private static final int BATCH_SIZE = 100;
    private final RecordReader recordReader;
    private final String workspaceId;
    private final String corporationId;
    private final String dataPlatformKeyId;
    private final String dataPlatformKey;
    private final String url;
    private final List<CdpCustomerBatchAddService.Param.Content> contentList = new ArrayList<>(
            BATCH_SIZE);


    public CdpCustomerBatchAddService(
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
            CdpCustomerBatchAddService.Param.Content content = new CdpCustomerBatchAddService.Param.Content()
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

    private void onSaveFail() {
        List<List<String>> table = new ArrayList<>();
        for (Param.Content content : contentList) {
            List<String> row = new ArrayList<>();
            row.add(JsonUtil.toJson(content.getIdentity()));
            row.add(JsonUtil.toJson(content.getProperty()));
            table.add(row);
        }
        try {
            OdpsUtil.saveData(table, "ads_cdp_customer_fail", "firmus_dataphin_prd_ads",
                    PartitionUtil.bizDate());
        } catch (Exception e) {
            log.error("save fail data to odps error:{}", e.getMessage(), e);
        }
    }

    private Long doSave(Long rowNum) {
        CdpCustomerBatchAddService.Param param = new CdpCustomerBatchAddService.Param()
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
            CdpCustomerBatchAdd.Response response = JsonUtil.fromJson(
                    resp,
                    CdpCustomerBatchAdd.Response.class);
            if (Objects.nonNull(response)
                    && response.getCode().equals(0)
                    && response.getData().getFailCount() <= 0) {
                log.info("send entity data to cdp success, process {} record", rowNum);
            } else {
                log.error(
                        "send entity data to cdp error:{}, url:{}, response:{}",
                        resp,
                        url,
                        response);
                onSaveFail();
                rowNum -= Objects.requireNonNull(response).getData().getFailCount();
                log.info("send entity data to cdp success, process {} record", rowNum);
            }
        } catch (Exception e) {
            log.error(
                    "send entity data to cdp error:{}, url:{}, payload:{}",
                    e.getMessage(),
                    url,
                    payload,
                    e);
            onSaveFail();
            rowNum -= contentList.size();
        } finally {
            contentList.clear();
        }
        return rowNum;
    }

    @lombok.Data
    @Accessors
    public static class Param {
        // 1 <= contents.size() <= 100
        private List<Content> contents;

        @Data
        public static class Content {
            // 客户身份
            private List<Map<String, String>> identity;
            // 客户属性
            private Map<String, Object> property;
        }
    }
}
