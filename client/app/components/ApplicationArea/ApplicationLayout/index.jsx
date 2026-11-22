import React, { useRef, useCallback, useState } from "react";
import PropTypes from "prop-types";
import DynamicComponent from "@/components/DynamicComponent";
import DesktopNavbar from "./DesktopNavbar";
import MobileNavbar from "./MobileNavbar";
import AiAssistant from "@/components/AiAssistant";
import usePresence from "./usePresence";

import "./index.less";

export default function ApplicationLayout({ children }) {
  usePresence();
  const [aiOpen, setAiOpen] = useState(false);
  const openAi = useCallback(() => setAiOpen(true), []);
  const mobileNavbarContainerRef = useRef();

  const getMobileNavbarPopupContainer = useCallback(() => mobileNavbarContainerRef.current, []);

  return (
    <React.Fragment>
      <DynamicComponent name="ApplicationWrapper">
        <div className="application-layout-side-menu">
          <DynamicComponent name="ApplicationDesktopNavbar">
            <DesktopNavbar onOpenAi={openAi} />
          </DynamicComponent>
        </div>
        <div className="application-layout-content">
          <nav className="application-layout-top-menu" ref={mobileNavbarContainerRef}>
            <DynamicComponent name="ApplicationMobileNavbar" getPopupContainer={getMobileNavbarPopupContainer}>
              <MobileNavbar getPopupContainer={getMobileNavbarPopupContainer} onOpenAi={openAi} />
            </DynamicComponent>
          </nav>
          {children}
          <AiAssistant open={aiOpen} onClose={() => setAiOpen(false)} />
        </div>
      </DynamicComponent>
    </React.Fragment>
  );
}

ApplicationLayout.propTypes = {
  children: PropTypes.node,
};

ApplicationLayout.defaultProps = {
  children: null,
};
