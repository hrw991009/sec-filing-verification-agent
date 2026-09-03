import createClient, { type Client, type ClientOptions } from "openapi-fetch";

import type { paths } from "./schema";

export type SecFilingVerificationApiClient = Client<paths>;

export function createSecFilingVerificationApiClient(
  options: ClientOptions = {},
): SecFilingVerificationApiClient {
  const { credentials = "include", ...remainingOptions } = options;

  return createClient<paths>({
    ...remainingOptions,
    credentials,
  });
}
