import { spawn } from "node:child_process";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { createInterface } from "node:readline/promises";
import { Type } from "typebox";
import {
  createAgentSession,
  ModelRuntime,
  SessionManager,
} from "@earendil-works/pi-coding-agent";

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const dataRoot = resolve(process.env.DEPLOY_AGENT_DATA_DIR || resolve(projectRoot, "..", "deploy-agent-data"));

function readArgs(argv) {
  const result = { prepareOnly: false, interactive: false };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--prepare-only") {
      result.prepareOnly = true;
    } else if (argument === "--interactive") {
      result.interactive = true;
    } else if (argument === "--package" || argument === "--connection") {
      const value = argv[++index];
      if (!value) throw new Error(`${argument} 缺少文件路径`);
      result[argument.slice(2)] = resolve(value);
    } else {
      throw new Error(`未知参数：${argument}`);
    }
  }
  if (!result.package || !result.connection) {
    throw new Error("用法：npm start -- --package <部署包> --connection <连接信息文件> [--prepare-only|--interactive]");
  }
  return result;
}

async function prepare(packagePath, connectionPath) {
  const python = process.env.DEPLOY_AGENT_PYTHON || "python";
  const arguments_ = [
    "-I",
    resolve(projectRoot, "storage", "init_storage.py"),
  ];
  await runPython(python, arguments_);
  const output = await runPython(python, [
    "-I",
    resolve(projectRoot, "src", "prepare.py"),
    "--package", packagePath,
    "--connection", connectionPath,
  ]);
  return JSON.parse(output.trim());
}

function runPython(command, args) {
  return new Promise((resolveResult, reject) => {
    const child = spawn(command, args, { cwd: projectRoot, shell: false, windowsHide: true });
    let stdout = "";
    let stderr = "";
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) resolveResult(stdout);
      else reject(new Error(stderr.trim() || `准备程序退出码：${code}`));
    });
  });
}

async function analyzeWithPi(prepared, interactive) {
  const inventory = JSON.parse(await readFile(prepared.inventory_path, "utf8"));
  const tool = {
    name: "task_context",
    label: "Task context",
    description: "读取当前任务中已脱敏的本地部署包盘点和连接发现结果。不能连接或修改服务器。",
    parameters: Type.Object({}),
    executionMode: "sequential",
    async execute() {
      return {
        content: [{ type: "text", text: JSON.stringify(inventory) }],
        details: { taskId: prepared.task_id },
      };
    },
  };

  const modelRuntime = await ModelRuntime.create();
  const { session } = await createAgentSession({
    cwd: projectRoot,
    modelRuntime,
    sessionManager: SessionManager.create(projectRoot, resolve(dataRoot, "pi-sessions")),
    tools: ["task_context"],
    customTools: [tool],
  });

  await runPython(process.env.DEPLOY_AGENT_PYTHON || "python", [
    "-I", resolve(projectRoot, "storage", "link_session.py"),
    "--task", prepared.task_id,
    "--session", session.sessionId,
  ]);

  session.subscribe((event) => {
    if (event.type === "message_update" && event.assistantMessageEvent.type === "text_delta") {
      process.stdout.write(event.assistantMessageEvent.delta);
    }
  });

  try {
    await session.prompt([
      `你是本地部署运维主 agent。任务 ID：${prepared.task_id}。`,
      "先调用 task_context，然后只根据已获得的本地证据分析部署包。",
      "列出已确认事实、待核实项、下一步服务器只读预检项目。",
      "远端连接和检查尚未执行；不要声称服务器状态已验证。",
      "不要要求、输出或猜测账号密码。现在没有任何可修改服务器的工具。",
    ].join("\n"));
    process.stdout.write("\n");
    if (interactive) {
      const input = createInterface({ input: process.stdin, output: process.stdout });
      try {
        while (true) {
          const message = (await input.question("你> ")).trim();
          if (message === "/exit" || message === "/quit") break;
          if (!message) continue;
          await session.prompt(message);
          process.stdout.write("\n");
        }
      } finally {
        input.close();
      }
    }
  } finally {
    session.dispose();
  }
}

async function main() {
  const args = readArgs(process.argv.slice(2));
  const prepared = await prepare(args.package, args.connection);
  console.log(`任务已创建：${prepared.task_id}`);
  console.log(`部署包 SHA-256：${prepared.package_sha256}`);
  console.log(`目标：${prepared.target}`);
  console.log(`本地盘点：${prepared.inventory_path}`);
  console.log("服务器预检：尚未执行");
  if (args.prepareOnly) return;
  await analyzeWithPi(prepared, args.interactive);
}

main().catch((error) => {
  console.error(`运行失败：${error instanceof Error ? error.message : String(error)}`);
  process.exitCode = 1;
});
