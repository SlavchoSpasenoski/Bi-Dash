import React from 'react'
import Link from 'components/Link'
export default function Header() {
  return <nav className="navbar navbar-fixed-top navbar-has-shadow"><div className="container" style={{display:'flex',alignItems:'center',justifyContent:'space-between',gap:20,padding:16,flexWrap:'wrap'}}>
    <Link to="/" aria-label="Bi-Dash home"><img src="/assets/images/bi-dash-wordmark.svg" alt="Bi-Dash" style={{width:170,height:38}} /></Link>
    <div style={{display:'flex',gap:24}}><Link to="/product/">Product</Link><Link to="/help/">Documentation</Link><Link to="https://github.com/SlavchoSpasenoski/Bi-Dash">GitHub</Link></div>
  </div></nav>
}
