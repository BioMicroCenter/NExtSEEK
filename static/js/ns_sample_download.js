/* The one sample-download client. Every download control in NExtSEEK calls this.
 *
 * Replaces three earlier paths: a POST form to /seek/admin/retrieve/, a $.post to
 * /seek/samples/download/ that returned a link to a file on disk, and a bespoke
 * fetch on the new retrieval page. See docs/sample-download-workflow.md.
 */
(function (window, document) {
  "use strict";

  var ENDPOINT = "/nextseek_api/admin/samples/retrieve/";

  function getCsrfToken() {
    var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function pad(n) {
    return (n < 10 ? "0" : "") + n;
  }

  function defaultFilename() {
    var d = new Date();
    return "download-samples-" + d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" +
           pad(d.getDate()) + "_" + pad(d.getHours()) + "-" + pad(d.getMinutes()) + ".xlsx";
  }

  function saveBlob(blob, filename) {
    var url = window.URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    window.setTimeout(function () {
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
    }, 0);
  }

  function closeProgress() {
    if (window.jQuery && window.jQuery.messager) {
      window.jQuery.messager.progress("close");
    }
  }

  /**
   * @param {string[]} identifiers Sample UIDs and/or numeric SEEK ids.
   * @param {{includeTree?: boolean, filename?: string}} [options]
   * @returns {Promise<void>}
   */
  function nsDownloadSamples(identifiers, options) {
    options = options || {};
    var ids = (identifiers || []).map(function (v) {
      return String(v).trim();
    }).filter(function (v) {
      return v.length > 0;
    });

    if (ids.length === 0) {
      window.alert("No sample in the table is selected for download.");
      return Promise.resolve();
    }

    var includeTree = options.includeTree !== false;

    if (window.jQuery && window.jQuery.messager) {
      window.jQuery.messager.progress({
        title: "Please wait",
        msg: includeTree ? "Retrieving samples and all associated samples..."
                         : "Retrieving samples..."
      });
    }

    return fetch(ENDPOINT, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": getCsrfToken()
      },
      body: JSON.stringify({
        identifiers: ids,
        output_format: "excel",
        include_tree: includeTree
      })
    }).then(function (response) {
      if (!response.ok) {
        throw new Error("Download failed (HTTP " + response.status + ")");
      }
      return response.blob();
    }).then(function (blob) {
      closeProgress();
      saveBlob(blob, options.filename || defaultFilename());
    }).catch(function (error) {
      closeProgress();
      console.error("Sample download error:", error);
      window.alert("Sample download failed: " + error.message);
    });
  }

  /* The datagrids render the uid column as an anchor, so row.uid is markup like
   * `<a href="...">MUS-230101ABC-1</a>`, not a bare UID. Both legacy collectors
   * pulled the text out with the same regex; do it in one place. */
  function nsExtractUid(raw) {
    if (raw === null || raw === undefined) {
      return "";
    }
    var s = String(raw);
    var m = s.match(/>([^<]+)</);
    return (m ? m[1] : s).trim();
  }

  /** Checked rows of an EasyUI datagrid -> UID strings. */
  function nsCollectSelectedUids(dg) {
    var rows = dg.datagrid("getRows");
    var uids = [];
    for (var i = 0; i < rows.length; i++) {
      if (rows[i].ck) {
        var uid = nsExtractUid(rows[i].uid);
        if (uid) {
          uids.push(uid);
        }
      }
    }
    return uids;
  }

  window.nsDownloadSamples = nsDownloadSamples;
  window.nsCollectSelectedUids = nsCollectSelectedUids;
  window.nsExtractUid = nsExtractUid;
})(window, document);
