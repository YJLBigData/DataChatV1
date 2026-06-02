package com.feihe.annotation;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * 表示当前实体的属性是另外一个属性的派生属性, 属性名称不一致, 属性值一致
 * <p>
 * 例如下面的实体类, nickName属性是name属性的派生属性
 * <pre>{@code
 * public class Example {
 *      @Derive("name")
 *      private String nickName;
 *      private String name;
 * }}</pre>
 */

@Target({ElementType.FIELD})
@Retention(RetentionPolicy.RUNTIME)
public @interface Derive {
    String value();
}
