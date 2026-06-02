package com.feihe.common;

import com.feihe.enumerate.Command;
import java.util.Arrays;
import org.apache.commons.cli.Option;

public class ApplicationParams {

    public static final String OPT_MC_AK = "mc-ak";
    public static final String OPT_MC_SK = "mc-sk";
    public static final String COMMAND = "command";
    public static final String CDP_SK = "cdp-sk";
    public static final String CDP_SK_ID = "cdp-sk-id";
    public static final String CDP_CORP_ID = "cdp-corp-id";
    public static final String PROJECT = "project";
    public static final String TABLE = "table";
    public static final String PARTITION = "partition";
    public static final String THREAD_NUM = "thread-num";

    public static final String DO_MAIN = "do-main";
    public static final String WORKSPACE_ID = "workspace-id";
    public static final String APP_KEY = "app-key";

    public static Option appKey() {
        return Option.builder()
                .longOpt(APP_KEY)
                .argName("APP_KEY")
                .desc("Cdp Event Report Key")
                .required(false)
                .hasArg(true)
                .get();
    }


    public static Option workspaceId() {
        return Option.builder()
                .longOpt(WORKSPACE_ID)
                .argName("WORKSPACE_ID")
                .desc("Cdp Workspace Id")
                .required(true)
                .hasArg(true)
                .get();
    }
    public static Option doMain() {
        return Option.builder()
                .longOpt(DO_MAIN)
                .argName("DO_MAIN")
                .desc("Do Main")
                .required(true)
                .hasArg(true)
                .get();
    }


    public static Option threadNum() {
        return Option.builder()
                .longOpt(THREAD_NUM)
                .argName("THREAD_NUM")
                .desc("Thread Num")
                .required(true)
                .hasArg(true)
                .get();
    }

    public static Option mcTablePartition() {
        return Option.builder()
                .longOpt(PARTITION)
                .argName("MAX_COMPUTE_TABLE_PARTITION")
                .desc("Max compute table partition")
                .required(true)
                .hasArg(true)
                .get();
    }

    public static Option accessId() {
        return Option.builder()
                .longOpt(OPT_MC_AK)
                .argName("ACCESS_ID")
                .desc("MaxCompute Access Id")
                .required(true)
                .hasArg(true)
                .get();
    }

    public static Option cdpCommand() {
        return Option.builder()
                .longOpt(COMMAND)
                .argName("COMMAND")
                .desc("Cdp System Api Command, Only Support " + Arrays.toString(Command.values()))
                .required(true)
                .hasArg(true)
                .get();

    }

    public static Option secretKey() {
        return Option.builder()
                .longOpt(OPT_MC_SK)
                .argName("SECRET_KEY")
                .desc("MaxCompute Secret Key")
                .required(true)
                .hasArg(true)
                .get();
    }


    public static Option cdpSecretKey() {
        return Option.builder()
                .longOpt(CDP_SK)
                .argName("CDP_SECRET_KEY")
                .desc("CDP Secret Key")
                .required(true)
                .hasArg(true)
                .get();
    }

    public static Option cdpSecretKeyId() {
        return Option.builder()
                .longOpt(CDP_SK_ID)
                .argName("CDP_SECRET_KEY_ID")
                .desc("CDP Secret Key Id")
                .required(true)
                .hasArg(true)
                .get();
    }

    public static Option cdpCorpId() {
        return Option.builder()
                .longOpt(CDP_CORP_ID)
                .argName("CDP_CORP_ID")
                .desc("CDP Corporation Id")
                .required(true)
                .hasArg(true)
                .get();
    }

    public static Option mcProject() {
        return Option.builder()
                .longOpt(PROJECT)
                .argName("MAX_COMPUTE_PROJECT")
                .desc("Max compute project name")
                .required(true)
                .hasArg(true)
                .get();
    }

    public static Option mcTable() {
        return Option.builder()
                .longOpt(TABLE)
                .argName("MAX_COMPUTE_TABLE")
                .desc("Max compute table name")
                .required(true)
                .hasArg(true)
                .get();
    }
}
