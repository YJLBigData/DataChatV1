package com.feihe;

import com.aliyun.odps.tunnel.TableTunnel;
import com.feihe.common.ApplicationParams;
import com.feihe.enumerate.Command;
import com.feihe.service.impl.CdpCustomerBatchAddService;
import com.feihe.service.impl.CdpCustomerBatchUpdateService;
import com.feihe.service.impl.CdpEntityBatchAddThread;
import com.feihe.service.impl.CdpEventsBehaviorTraceService;
import com.feihe.service.impl.OdpsService;
import com.feihe.util.PartitionUtil;
import com.google.common.base.CaseFormat;
import lombok.extern.slf4j.Slf4j;
import org.apache.commons.cli.CommandLine;
import org.apache.commons.cli.CommandLineParser;
import org.apache.commons.cli.DefaultParser;
import org.apache.commons.cli.Options;
import org.apache.commons.cli.ParseException;
import org.apache.commons.cli.help.HelpFormatter;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.atomic.LongAdder;

@Slf4j
@SuppressWarnings({"java:S3740", "java:S6541"})
public class FeiHeCdpApplication {
    public static String ak;

    public static String sk;

    public static void main(String[] args) {
        Options options = new Options();
        options.addOption(ApplicationParams.accessId());
        options.addOption(ApplicationParams.secretKey());
        options.addOption(ApplicationParams.cdpCommand());
        options.addOption(ApplicationParams.cdpSecretKey());
        options.addOption(ApplicationParams.cdpSecretKeyId());
        options.addOption(ApplicationParams.cdpCorpId());
        options.addOption(ApplicationParams.mcProject());
        options.addOption(ApplicationParams.mcTable());
        options.addOption(ApplicationParams.mcTablePartition());
        options.addOption(ApplicationParams.threadNum());
        options.addOption(ApplicationParams.doMain());
        options.addOption(ApplicationParams.workspaceId());
        options.addOption(ApplicationParams.appKey());
        try {
            // 解析命令行参数
            CommandLineParser parser = new DefaultParser();
            CommandLine cmd = parser.parse(options, args);
            // 获取参数值
            String mcAk = cmd.getOptionValue(ApplicationParams.OPT_MC_AK);
            ak = cmd.getOptionValue(ApplicationParams.OPT_MC_AK);
            String mcSk = cmd.getOptionValue(ApplicationParams.OPT_MC_SK);
            sk = cmd.getOptionValue(ApplicationParams.OPT_MC_SK);
            String command = cmd.getOptionValue(ApplicationParams.COMMAND);
            String cdpSk = cmd.getOptionValue(ApplicationParams.CDP_SK);
            String cdpSkId = cmd.getOptionValue(ApplicationParams.CDP_SK_ID);
            String corpId = cmd.getOptionValue(ApplicationParams.CDP_CORP_ID);
            String project = cmd.getOptionValue(ApplicationParams.PROJECT);
            String table = cmd.getOptionValue(ApplicationParams.TABLE);
            String partition = cmd.getOptionValue(ApplicationParams.PARTITION);
            String doMain = cmd.getOptionValue(ApplicationParams.DO_MAIN);
            String workspaceId = cmd.getOptionValue(ApplicationParams.WORKSPACE_ID);
            int threadNum = Integer.parseInt(cmd.getOptionValue(ApplicationParams.THREAD_NUM));
            String eventAppKey = cmd.getOptionValue(ApplicationParams.APP_KEY);


            String entityKey = CaseFormat.LOWER_UNDERSCORE
                    .to(CaseFormat.UPPER_CAMEL, table)
                    .replace("AdsCdp", "");
            if (table.endsWith("_di") && Command.ENTITY.equals(Command.valueOf(command))) {
                entityKey = entityKey.substring(0, entityKey.length() - 2);
            }

            if (table.equals("ads_cdp_wmstaff")) {
                entityKey = "wmstaff";
            }

            log.info("start FeiHeCdpApplication, command:{}", command);

            OdpsService odpsService = new OdpsService(mcAk, mcSk);
            TableTunnel.DownloadSession downloadSession = odpsService.createDownloadSession(
                    project,
                    table,
                    PartitionUtil.sqlBizDate(partition));
            long totalRowCount = downloadSession.getRecordCount();
            if (totalRowCount <= 0) {
                log.info("{}.{} partition({}) has no input data", project, table, partition);
                return;
            }
            log.info("{}.{} total row count:{}", project, table, totalRowCount);
            ExecutorService pool = Executors.newFixedThreadPool(threadNum);
            List<Callable<Long>> callableList = new ArrayList<>();
            long step = totalRowCount / threadNum;
            for (int i = 0; i < threadNum - 1; i++) {
                switch (Command.valueOf(command)) {
                    case CUSTOMER:
                        callableList.add(new CdpCustomerBatchAddService(
                                downloadSession.openRecordReader(step * i, step),
                                doMain,
                                Command.CUSTOMER.getApi(),
                                workspaceId,
                                corpId,
                                cdpSkId,
                                cdpSk)
                        );
                        break;
                    case CUSTOMER_UPDATE:
                        callableList.add(new CdpCustomerBatchUpdateService(
                                downloadSession.openRecordReader(step * i, step),
                                doMain,
                                Command.CUSTOMER_UPDATE.getApi(),
                                workspaceId,
                                corpId,
                                cdpSkId,
                                cdpSk
                        ));
                        break;
                    case ENTITY:
                        callableList.add(new CdpEntityBatchAddThread(
                                downloadSession.openRecordReader(step * i, step),
                                entityKey,
                                workspaceId,
                                doMain,
                                Command.ENTITY.getApi(),
                                cdpSkId,
                                cdpSk,
                                corpId)
                        );
                        break;
                    case EVENT_REALTIME:
                        callableList.add(
                                new CdpEventsBehaviorTraceService(
                                        downloadSession.openRecordReader(step * i, step),
                                        Command.EVENT_REALTIME.getApi(),
                                        doMain,
                                        eventAppKey)
                        );
                        break;
                    case EVENT_HISTORY:
                        callableList.add(
                                new CdpEventsBehaviorTraceService(
                                        downloadSession.openRecordReader(step * i, step),
                                        Command.EVENT_HISTORY.getApi(),
                                        doMain,
                                        eventAppKey)
                        );
                        break;
                    case EVENT_BACKTRACK:
                        callableList.add(
                                new CdpEventsBehaviorTraceService(
                                        downloadSession.openRecordReader(step * i, step),
                                        Command.EVENT_BACKTRACK.getApi(),
                                        doMain,
                                        eventAppKey)
                        );
                        break;
                }
            }
            switch (Command.valueOf(command)) {
                case CUSTOMER:
                    callableList.add(new CdpCustomerBatchAddService(
                            downloadSession.openRecordReader(
                                    step * (threadNum - 1),
                                    totalRowCount - ((threadNum - 1) * step)),
                            doMain,
                            Command.CUSTOMER.getApi(),
                            workspaceId,
                            corpId,
                            cdpSkId,
                            cdpSk)
                    );
                    break;
                case CUSTOMER_UPDATE:
                    callableList.add(new CdpCustomerBatchUpdateService(
                            downloadSession.openRecordReader(
                                    step * (threadNum - 1),
                                    totalRowCount - ((threadNum - 1) * step)),
                            doMain,
                            Command.CUSTOMER_UPDATE.getApi(),
                            workspaceId,
                            corpId,
                            cdpSkId,
                            cdpSk
                    ));
                    break;
                case ENTITY:
                    callableList.add(new CdpEntityBatchAddThread(
                            downloadSession.openRecordReader(
                                    step * (threadNum - 1),
                                    totalRowCount - ((threadNum - 1) * step)),
                            entityKey, workspaceId, doMain, Command.ENTITY.getApi(),
                            cdpSkId,
                            cdpSk,
                            corpId)
                    );
                    break;
                case EVENT_REALTIME:
                    callableList.add(
                            new CdpEventsBehaviorTraceService(
                                    downloadSession.openRecordReader(
                                            step * (threadNum - 1),
                                            totalRowCount - ((threadNum - 1) * step)),
                                    Command.EVENT_REALTIME.getApi(),
                                    doMain,
                                    eventAppKey)
                    );
                    break;
                case EVENT_HISTORY:
                    callableList.add(
                            new CdpEventsBehaviorTraceService(
                                    downloadSession.openRecordReader(
                                            step * (threadNum - 1),
                                            totalRowCount - ((threadNum - 1) * step)),
                                    Command.EVENT_HISTORY.getApi(),
                                    doMain,
                                    eventAppKey)
                    );
                    break;
                case EVENT_BACKTRACK:
                    callableList.add(
                            new CdpEventsBehaviorTraceService(
                                    downloadSession.openRecordReader(
                                            step * (threadNum - 1),
                                            totalRowCount - ((threadNum - 1) * step)),
                                    Command.EVENT_BACKTRACK.getApi(),
                                    doMain,
                                    eventAppKey)
                    );
                    break;
            }
            Long downloadNum = 0L;
            List<Future<Long>> recordNum = pool.invokeAll(callableList);
            for (Future<Long> num : recordNum) downloadNum += num.get();
            log.info(
                    "{}.{} partition: {} process {} records, {} records complete",
                    project,
                    table,
                    partition,
                    totalRowCount,
                    downloadNum);
            pool.shutdown();
        } catch (Exception e) {
            log.error("failed to run cdp application: {}", e.getMessage(), e);
            if (e instanceof InterruptedException) {
                Thread.currentThread().interrupt();
            }
            if (e instanceof ParseException) {
                HelpFormatter helpFormatter = HelpFormatter.builder()
                        .setShowSince(false)
                        .get();
                try {
                    helpFormatter
                            .printHelp("java -jar FeiHeCDP.jar", null, options, null, true);
                } catch (IOException ex) {
                    ex.printStackTrace();
                }
                System.exit(1);
            }
        }
    }
}
