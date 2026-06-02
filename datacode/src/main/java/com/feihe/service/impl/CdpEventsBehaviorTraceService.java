package com.feihe.service.impl;

import com.aliyun.odps.data.Record;
import com.aliyun.odps.data.RecordReader;
import com.feihe.dto.CdpEventsBehaviorLog;
import com.feihe.enumerate.Command;
import com.feihe.util.HttpUtil;
import com.feihe.util.JsonUtil;
import lombok.extern.slf4j.Slf4j;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.Callable;
import java.util.concurrent.atomic.LongAdder;

/**
 * <a href="https://tmc.qidian.qq.com/base/console/doc/14604?version=20260203">服务端历史行为数据上报</a>
 */
@Slf4j
@SuppressWarnings({"java:S3011", "unchecked"})
public class CdpEventsBehaviorTraceService implements Callable<Long> {
    private static final Integer BATCH_SIZE = 100;
    private final List<CdpEventsBehaviorLog.Param.EventsData> dataList = new ArrayList<>(BATCH_SIZE);
    private final RecordReader recordReader;
    private final String appKey;
    private final String doMain;
    private final String apiSuffix;
    public CdpEventsBehaviorTraceService(
            RecordReader recordReader,
            String apiSuffix,
            String doMain,
            String appKey) {
        this.recordReader = recordReader;
        this.doMain = doMain;
        this.apiSuffix = apiSuffix;
        this.appKey = appKey;
    }

    @Override
    public Long call() {
        Long recordNum = 0L;
        try {
            Record row;
            while ((row = recordReader.read()) != null) {
                recordNum++;
                Map<String, Object> properties = (Map<String, Object>) row.get("properties");
                Map<String, Object> account = (Map<String, Object>) row.get("account");
                CdpEventsBehaviorLog.Param.EventsData eventsData = new CdpEventsBehaviorLog.Param.EventsData()
                        .setTime(row.getString("time"))
                        .setEvent(row.getString("event"))
                        .setProperties(properties)
                        .setAccount(account);
                if (this.apiSuffix.equals(Command.EVENT_BACKTRACK.getApi())) {
                    String eventId = row.getString("eventid");
                    eventsData = eventsData.setEventId(eventId);
                }
                dataList.add(eventsData);
                if (dataList.size() >= BATCH_SIZE) {
                    recordNum = doSave(recordNum);
                }
            }
            if (!dataList.isEmpty()) {
                recordNum = doSave(recordNum);
            }
            recordReader.close();
        } catch (IOException e) {
            log.error("read record error:{}", e.getMessage(), e);
        }
        return recordNum;
    }

    public Long doSave(Long recordNum) {
        String dataJson = JsonUtil.toJson(dataList);
        String url = this.doMain.concat(this.apiSuffix);
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
                log.info("send entity data to cdp success, process {} record", recordNum);
            } else {
                log.error(
                        "send entity data to cdp error:{}, url:{}, response:{}",
                        resp,
                        url,
                        response);
                log.info("send entity data to cdp success, process {} record", recordNum);
            }
        } catch (Exception e) {
            log.error(
                    "send entity data to cdp error:{}, url:{}, payload:{}",
                    e.getMessage(),
                    url,
                    payload,
                    e);
            recordNum -= dataList.size();
        } finally {
            dataList.clear();
        }
        return recordNum;
    }
}
