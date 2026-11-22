import { map } from "lodash";
import React from "react";
import Modal from "antd/lib/modal";
import { Auth, currentUser } from "@/services/auth";

const SESSION_RESTORED_MESSAGE = "redash_session_restored";

export function notifySessionRestored() {
  if (window.opener) {
    window.opener.postMessage({ type: SESSION_RESTORED_MESSAGE, user_id: currentUser.id }, window.location.origin);
  }
}

function getPopupPosition(width, height) {
  const windowLeft = window.screenX;
  const windowTop = window.screenY;
  const windowWidth = window.innerWidth;
  const windowHeight = window.innerHeight;

  return {
    left: Math.floor((windowWidth - width) / 2 + windowLeft),
    top: Math.floor((windowHeight - height) / 2 + windowTop),
    width: Math.floor(width),
    height: Math.floor(height),
  };
}

function showRestoreSessionPrompt(loginUrl, onSuccess, onIdentityChanged) {
  let popup = null;
  const originalUserId = currentUser.id;

  Modal.warning({
    content: "Your session has expired. Please login to continue.",
    okText: (
      <React.Fragment>
        Login <i className="fa fa-external-link m-r-5" aria-hidden="true" />
        <span className="sr-only">(opens in a new tab)</span>
      </React.Fragment>
    ),
    centered: true,
    mask: true,
    maskClosable: false,
    keyboard: false,
    onOk: (closeModal) => {
      if (popup && !popup.closed) {
        popup.focus();
        return; // popup already shown
      }

      const popupOptions = {
        ...getPopupPosition(640, 640),
        menubar: "no",
        toolbar: "no",
        location: "yes",
        resizable: "yes",
        scrollbars: "yes",
        status: "yes",
      };

      popup = window.open(loginUrl, "Restore Session", map(popupOptions, (value, key) => `${key}=${value}`).join(","));

      const handlePostMessage = (event) => {
        if (
          popup &&
          event.source === popup &&
          event.origin === window.location.origin &&
          event.data?.type === SESSION_RESTORED_MESSAGE
        ) {
          if (popup) {
            popup.close();
          }
          popup = null;
          window.removeEventListener("message", handlePostMessage);
          closeModal();
          if (Number.isInteger(originalUserId) && originalUserId > 0 && event.data.user_id === originalUserId) {
            onSuccess();
          } else {
            onIdentityChanged();
          }
        }
      };

      window.addEventListener("message", handlePostMessage, false);
    },
  });
}

let restoreSessionPromise = null;

export function restoreSession() {
  if (!restoreSessionPromise) {
    restoreSessionPromise = new Promise((resolve, reject) => {
      showRestoreSessionPrompt(
        Auth.getLoginUrl(),
        () => {
          restoreSessionPromise = null;
          resolve();
        },
        () => {
          restoreSessionPromise = null;
          reject(new Error("The authenticated account changed. Reloading the session."));
          Auth.reloadSession();
        }
      );
    });
  }

  return restoreSessionPromise;
}
