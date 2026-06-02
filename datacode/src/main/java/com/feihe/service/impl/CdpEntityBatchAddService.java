package com.feihe.service.impl;

import com.aliyun.odps.data.Record;
import com.aliyun.odps.data.RecordReader;
import com.feihe.dto.CdpEntityBatchAdd;
import com.feihe.util.CdpQueryUtil;
import com.feihe.util.HttpUtil;
import com.feihe.util.JsonUtil;
import lombok.extern.slf4j.Slf4j;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.Callable;

/**
 * <a href="https://tmc.qidian.qq.com/base/console/doc/14629?version=20260203">实体批量创建</a>
 */
@Slf4j
@SuppressWarnings("unchecked")
public class CdpEntityBatchAddService implements Callable<Long> {
    private final String url;
    private static final int BATCH_SIZE = 500;
    private final List<CdpEntityBatchAdd.Param.Content> contentList = new ArrayList<>(BATCH_SIZE);
    private final RecordReader recordReader;
    private final String entityKey;

    private final String workspaceId;
    private final String dataPlatformKeyId;
    private final String dataPlatformKey;
    private final String corporationId;

    public CdpEntityBatchAddService(
            RecordReader recordReader,
            String entityKey,
            String workspaceId,
            String doMain,
            String apiSuffix,
            String corporationId,
            String dataPlatformKeyId,
            String dataPlatformKey
            ) {
        this.recordReader = recordReader;
        this.entityKey = entityKey;
        this.url = doMain.concat(apiSuffix);
        this.workspaceId = workspaceId;
        this.dataPlatformKeyId = dataPlatformKeyId;
        this.corporationId = corporationId;
        this.dataPlatformKey = dataPlatformKey;
    }

    @Override
    public Long call() {
        Long rowNum = 0L;
        try {
            Record row;
            while ((row = recordReader.read()) != null) {
                rowNum++;
                Map<String, Object> property = (Map<String, Object>) row.get("property");
                CdpEntityBatchAdd.Param.Content content = new CdpEntityBatchAdd.Param.Content()
                        .setProperty(property);
                contentList.add(content);
                if (contentList.size() >= BATCH_SIZE) {
                    doSave();
                }
            }
            if (!contentList.isEmpty()) {
                doSave();
            }
            recordReader.close();
        } catch (IOException e) {
            log.error("read record error:{}", e.getMessage(), e);
        }
        return rowNum;
    }

    private void doSave() {
        CdpEntityBatchAdd.Param param = new CdpEntityBatchAdd.Param()
                .setEntityKey(this.entityKey)
                .setContents(contentList);
        String payload = JsonUtil.toJson(param);
        try {
            String resp = HttpUtil.post(
                    this.url,
                    CdpQueryUtil.generatePathParam(this.workspaceId),
                    CdpQueryUtil.generateHeader(this.corporationId, this.dataPlatformKeyId,this.dataPlatformKey),
                    payload);
            CdpEntityBatchAdd.Response response = JsonUtil.fromJson(
                    resp,
                    CdpEntityBatchAdd.Response.class);
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
        } finally {
            contentList.clear();
        }
    }
}
