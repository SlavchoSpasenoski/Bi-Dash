import React from "react";
import Link from "@/components/Link";
import { clientConfig, currentUser } from "@/services/auth";
import frontendVersion from "@/version.json";

export default function VersionInfo() {
  return (
    <React.Fragment>
      <div>
        Bi-Dash<br />
        Engine version: {clientConfig.version}
        {frontendVersion !== clientConfig.version && ` (${frontendVersion.substring(0, 8)})`}
        <br />
        © 2026 Slavcho Spasenoski · original Bi-Dash contributions
        <br />
        <Link href="/static/licenses/NOTICE.txt" target="_blank" rel="noopener">Open-source notices</Link>
        {" · "}
        <Link href="/static/licenses/LICENSE.txt" target="_blank" rel="noopener">License</Link>
      </div>
      {clientConfig.newVersionAvailable && currentUser.hasPermission("super_admin") && (
        <div className="m-t-10">
          <Link href="https://version.redash.io/" className="update-available" target="_blank" rel="noopener">
            Base engine update available <i className="fa fa-external-link m-l-5" aria-hidden="true" />
            <span className="sr-only">(opens in a new tab)</span>
          </Link>
        </div>
      )}
    </React.Fragment>
  );
}
