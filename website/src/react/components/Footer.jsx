import React from 'react'
import Link from 'components/Link'
export default function Footer() {
  return <footer className="footer"><div className="container"><p>© 2026 Slavcho Spasenoski · original Bi-Dash contributions</p>
    <p><Link to="/terms/">License and notices</Link> · <Link to="/privacy/">Privacy and data flow</Link> · <Link to="https://github.com/SlavchoSpasenoski/Bi-Dash">Source</Link></p>
    <p>Based on the open-source Redash project. Upstream and third-party rights are preserved.</p>
  </div></footer>
}
