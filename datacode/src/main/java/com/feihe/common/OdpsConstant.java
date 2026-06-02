package com.feihe.common;

public class OdpsConstant {
    // odps 配置参数
    public static final String DRIVER_NAME = "com.aliyun.odps.jdbc.OdpsDriver";
    public static final String END_POINT = "http://service.cn-beijing.maxcompute.aliyun.com/api";
    public static final String DEFAULT_PROJECT = "firmus_dataphin_prd_ods";
    public static final String TUNNEL_SERVER = "https://dt.cn-beijing.maxcompute.aliyun.com";
    public static final String ODPS_URL = "jdbc:odps:http://service.cn-beijing.maxcompute.aliyun.com/api?project=firmus_dataphin_prd_ods";

    // 蚁盾配置参数
    public static final String YI_DUN_AK = "AC63S9KmoXto3dqw";

    public static String aliYunDataPlatformSk() {
        return System.getenv("ALIYUN_DATA_PLATFORM_SK");
    }

    public static String aliYunDataPlatformAk(){
        return System.getenv("ALIYUN_DATA_PLATFORM_AK");
    }

    public static String yiDunSk(){
        return System.getenv("YI_DUN_SK");
    }

    private OdpsConstant() {
    }
}
