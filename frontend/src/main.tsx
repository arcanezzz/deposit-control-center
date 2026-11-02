import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { App as AntApp, ConfigProvider } from 'antd'
import heIL from 'antd/locale/he_IL'
import App from './App'
import { EmployeeProvider } from './context/EmployeeContext'
import './format' // dayjs plugins + Hebrew locale

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider direction="rtl" locale={heIL} theme={{ token: { fontFamily: "'Segoe UI', 'Rubik', Arial, sans-serif" } }}>
      <AntApp>
        <BrowserRouter>
          <EmployeeProvider>
            <App />
          </EmployeeProvider>
        </BrowserRouter>
      </AntApp>
    </ConfigProvider>
  </React.StrictMode>,
)
