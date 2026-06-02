package com.feihe.util;

import com.feihe.exception.CdpCallFailedException;
import lombok.extern.slf4j.Slf4j;
import okhttp3.*;
import org.apache.commons.lang.time.StopWatch;

import java.io.IOException;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.TimeUnit;

@Slf4j
public class HttpUtil {

    private HttpUtil() {
    }

    private static final MediaType JSON_MEDIA_TYPE = MediaType.parse(
            "application/json; charset=utf-8");
    private static final OkHttpClient client = new OkHttpClient.Builder()
            .connectTimeout(3, TimeUnit.MINUTES)
            .readTimeout(3, TimeUnit.MINUTES)
            .writeTimeout(3, TimeUnit.MINUTES)
            .build();

    public static String post(
            String urlStr,
            Map<String, String> params,
            Map<String, String> headers,
            String payload
    ) throws IOException {
        if (Objects.nonNull(params) && !params.isEmpty()) {
            HttpUrl httpUrl = HttpUrl.parse(urlStr);
            if (httpUrl == null) {
                throw new IllegalArgumentException("Invalid URL: " + urlStr);
            }
            HttpUrl.Builder urlBuilder = httpUrl.newBuilder();
            params.forEach(urlBuilder::addQueryParameter);
            urlStr = urlBuilder.build().toString();
        }

        RequestBody requestBody = RequestBody.create(new byte[0], null);
        if (Objects.nonNull(payload) && !payload.isEmpty()) {
            requestBody = RequestBody.create(
                    payload,
                    JSON_MEDIA_TYPE);
        }

        Request.Builder requestBuilder = new Request.Builder()
                .url(urlStr)
                .post(requestBody);

        if (Objects.nonNull(headers)) {
            headers.forEach(requestBuilder::addHeader);
        }

        Request request = requestBuilder.build();

        log.debug(
                "HTTP POST request to {}, params: {}, headers: {}, payload: {}",
                urlStr,
                params,
                headers,
                payload);
        StopWatch stopWatch = new StopWatch();
        stopWatch.start();
        try (Response response = client.newCall(request).execute()) {
            stopWatch.stop();
            String responseBody = response.body() != null ? response.body().string() : "";
            int statusCode = response.code();
            log.debug(
                    "HTTP POST success to {}, status: {}, response: {}, cost: {}ms",
                    urlStr,
                    statusCode,
                    responseBody,
                    stopWatch.getTime()
            );
            if (!response.isSuccessful()) {
                throw new CdpCallFailedException("Unexpected code " + statusCode);
            }
            return responseBody;
        }
    }
}
