import UM 1.5 as UM // This allows you to use all of Uranium's built-in QML items.
import QtQuick 2.2 // This allows you to use QtQuick's built-in QML items.
import QtQuick.Controls 1.1
import Cura 1.1 as Cura

UM.Dialog
{
  id: nextChunkDialog

  width: 500 * screenScaleFactor
  height: 100 * screenScaleFactor
  minimumWidth: 500 * screenScaleFactor
  minimumHeight: 100 * screenScaleFactor

  Label
  {
    text: "something something something"
    wrapMode: Label.WordWrap
    width: Math.floor(parent.width * .9)
    anchors.horizontalCenter: autoUploadDialog.horizontalCenter
  }

  // onRejected: {
  //   main.cancelAutoUpload();
  // }
  //
  // onAccepted: {
  //   main.startAutoUpload();
  // }

  rightButtons: [
    Button {
      text: "Terminate Process"
      onClicked:
      {
        nextChunkDialog.reject()
        nextChunkDialog.hide()
      }
    },
    Button {
      text: "Upload Next"
      onClicked: {
        nextChunkDialog.accept()
        nextChunkDialog.hide()
      }
    }
  ]
}
