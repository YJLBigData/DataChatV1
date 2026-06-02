package com.feihe.service.impl;

import com.aliyun.odps.Odps;
import com.aliyun.odps.PartitionSpec;
import com.aliyun.odps.account.Account;
import com.aliyun.odps.account.AliyunAccount;
import com.aliyun.odps.tunnel.TableTunnel;
import com.aliyun.odps.tunnel.TunnelException;
import com.feihe.common.OdpsConstant;
import com.feihe.exception.ServiceException;

public class OdpsService {
    private final TableTunnel tableTunnel;

    public OdpsService(String accessKey, String secretKey) {
        Account account = new AliyunAccount(
                accessKey, secretKey);
        Odps odps = new Odps(account);
        odps.setEndpoint(OdpsConstant.END_POINT);
        odps.setDefaultProject(OdpsConstant.DEFAULT_PROJECT);
        this.tableTunnel = new TableTunnel(odps);
        this.tableTunnel.setEndpoint(OdpsConstant.TUNNEL_SERVER);
    }

    public TableTunnel.DownloadSession createDownloadSession(
            String projectName,
            String tableName,
            String partition) {
        PartitionSpec partitionSpec = new PartitionSpec(partition);
        try {
            return tableTunnel.createDownloadSession(
                    projectName,
                    tableName,
                    partitionSpec);
        } catch (TunnelException e) {
            throw new ServiceException(e.getMessage(), e);
        }
    }
}
