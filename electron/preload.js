'use strict'

const { contextBridge } = require('electron')

contextBridge.exposeInMainWorld('forgeElectron', {
  version: process.versions.electron,
})
