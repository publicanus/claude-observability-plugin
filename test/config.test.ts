import { describe, expect, it } from "vitest";

import { getConfig } from "../src/config.js";

const NONEXISTENT = "/nonexistent-dir-for-tests";

function baseOpts(env: Record<string, string | undefined>) {
  // Point home/cwd at a path with no langfuse.json so only env is exercised.
  return { home: NONEXISTENT, cwd: NONEXISTENT, env };
}

describe("getConfig", () => {
  it("applies defaults when nothing is set", async () => {
    const config = await getConfig(baseOpts({}));
    expect(config.base_url).toBe("https://us.cloud.langfuse.com");
    expect(config.max_chars).toBe(20_000);
    expect(config.debug).toBe(false);
    expect(config.public_key).toBeUndefined();
  });

  it("reads plain LANGFUSE_* environment variables", async () => {
    const config = await getConfig(
      baseOpts({ LANGFUSE_PUBLIC_KEY: "pk", LANGFUSE_SECRET_KEY: "sk" }),
    );
    expect(config.public_key).toBe("pk");
    expect(config.secret_key).toBe("sk");
  });

  it("prefers the CLAUDE_PLUGIN_OPTION_* form over the plain variable", async () => {
    const config = await getConfig(
      baseOpts({
        LANGFUSE_PUBLIC_KEY: "plain",
        CLAUDE_PLUGIN_OPTION_LANGFUSE_PUBLIC_KEY: "fromPlugin",
      }),
    );
    expect(config.public_key).toBe("fromPlugin");
  });

  it("falls back to CC_LANGFUSE_* aliases", async () => {
    const config = await getConfig(baseOpts({ CC_LANGFUSE_SECRET_KEY: "sk" }));
    expect(config.secret_key).toBe("sk");
  });

  it("coerces booleans, integers, and tags", async () => {
    const config = await getConfig(
      baseOpts({
        CC_LANGFUSE_DEBUG: "true",
        CC_LANGFUSE_MAX_CHARS: "500",
        CC_LANGFUSE_TAGS: "a, b ,c",
      }),
    );
    expect(config.debug).toBe(true);
    expect(config.max_chars).toBe(500);
    expect(config.tags).toEqual(["a", "b", "c"]);
  });
});
