import createClient, { type Client, type ClientOptions } from "openapi-fetch";

import type { paths } from "./schema";

export type IndustryPlatformApiClient = Client<paths>;

export function createIndustryPlatformApiClient(
  options: ClientOptions = {},
): IndustryPlatformApiClient {
  const { credentials = "include", ...remainingOptions } = options;

  return createClient<paths>({
    ...remainingOptions,
    credentials,
  });
}
