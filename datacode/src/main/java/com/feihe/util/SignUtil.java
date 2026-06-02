package com.feihe.util;

import lombok.extern.slf4j.Slf4j;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

import java.util.Base64;


@Slf4j
public class SignUtil {

    private SignUtil() {

    }

    private static final String SHA265 = "HmacSHA256";

    private static byte[] sign(String source, String secret) {
        byte[] bytes = {};
        try {
            Mac sha256HMAC = Mac.getInstance(SHA265);
            SecretKeySpec secretKey = new SecretKeySpec(secret.getBytes(), SHA265);
            sha256HMAC.init(secretKey);
            bytes = sha256HMAC.doFinal(source.getBytes());
        } catch (Exception e) {
            log.error("sign error:{}", e.getMessage(), e);
        }
        return bytes;
    }

    // HmacSHA256加密，base64编码
    public static String signToBase64BySHA256(String source, String secret) {
        byte[] hmacSHA256Bytes = sign(source, secret);
        return Base64.getEncoder().encodeToString(hmacSHA256Bytes);
    }
}
