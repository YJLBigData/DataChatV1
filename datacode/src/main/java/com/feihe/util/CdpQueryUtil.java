package com.feihe.util;

import java.util.HashMap;
import java.util.Map;

public class CdpQueryUtil {

    private static final String PATH_PARAM = "bizId";

    private static final String CORPORATION_ID = "corporationId";

    private static final String SECRET_ID = "secretId";

    private static final String TIMESTAMP = "timestamp";

    private static final String SIGN = "sign";

    private CdpQueryUtil() {
    }

    public static Map<String, String> generatePathParam(String workspaceId) {
        Map<String, String> pathParam = new HashMap<>();
        pathParam.put(PATH_PARAM, workspaceId);
        return pathParam;
    }

    public static Map<String, String> generateHeader(String corporationId,String dataPlatformKeyId,String dataPlatformKey) {
        String currentTimeSeconds = DateUtil.currentTimeSecondsStr();
        String sign = SignUtil.signToBase64BySHA256(
                corporationId.concat(currentTimeSeconds),
                dataPlatformKey);
        Map<String, String> header = new HashMap<>();
        header.put(CORPORATION_ID, corporationId);
        header.put(TIMESTAMP, currentTimeSeconds);
        header.put(SECRET_ID, dataPlatformKeyId);
        header.put(SIGN, sign);
        return header;
    }
}
