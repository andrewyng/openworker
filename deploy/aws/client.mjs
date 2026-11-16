import { execFile } from "node:child_process";
import { promisify } from "node:util";
const exec = promisify(execFile);
export const account = process.env.OW_AWS_ACCOUNT_ID || "",
  region = process.env.OW_AWS_REGION || "us-west-2";
export async function connect() {
  if (!/^\d{12}$/.test(account))
    throw Error("Set OW_AWS_ACCOUNT_ID to the explicitly authorized target account");
  const roleArn = process.env.OW_AWS_ROLE_ARN;
  if (!roleArn || !roleArn.startsWith(`arn:aws:iam::${account}:role/`))
    throw Error("Set OW_AWS_ROLE_ARN to an approved role in the target account");
  const raw = async (args, env = process.env) => {
    const { stdout } = await exec(
      "aws",
      [...args, "--region", region, "--output", "json", "--no-cli-pager"],
      {
        env: {
          ...env,
          AWS_PAGER: "",
          AWS_RETRY_MODE: "standard",
          AWS_MAX_ATTEMPTS: "2",
        },
        timeout: 60000,
        maxBuffer: 16 * 1024 * 1024,
      },
    );
    return stdout.trim() ? JSON.parse(stdout) : null;
  };
  const { Credentials: c } = await raw([
    "sts",
    "assume-role",
    "--role-arn",
    roleArn,
    "--role-session-name",
    "ow-taskdesk-experiment",
    "--duration-seconds",
    "3600",
  ]);
  const env = {
    ...process.env,
    AWS_ACCESS_KEY_ID: c.AccessKeyId,
    AWS_SECRET_ACCESS_KEY: c.SecretAccessKey,
    AWS_SESSION_TOKEN: c.SessionToken,
  };
  delete env.AWS_PROFILE;
  delete env.AWS_DEFAULT_PROFILE;
  const aws = (args) => raw(args, env);
  if ((await aws(["sts", "get-caller-identity"])).Account !== account)
    throw Error("Unexpected AWS account");
  return { aws, env };
}
