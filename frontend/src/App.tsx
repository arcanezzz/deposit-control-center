import { Layout, Menu, Select, Space, Tooltip, Typography } from 'antd'
import { BankOutlined, FileSearchOutlined, SendOutlined, UserOutlined } from '@ant-design/icons'
import { Link, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { useEmployee } from './context/EmployeeContext'
import { ExecutePage } from './pages/ExecutePage'
import { InvestigatePage } from './pages/InvestigatePage'

const { Header, Content, Footer } = Layout

export default function App() {
  const { employees, employee, setEmployeeId } = useEmployee()
  const { pathname } = useLocation()

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ display: 'flex', alignItems: 'center', gap: 24, flexWrap: 'wrap', height: 'auto', lineHeight: '64px' }}>
        <Space style={{ color: '#fff', fontSize: 18, fontWeight: 600 }}>
          <BankOutlined /> מרכז שליטה בהפקדות
        </Space>
        <Menu
          theme="dark"
          mode="horizontal"
          selectedKeys={[pathname.startsWith('/investigate') ? 'investigate' : 'execute']}
          style={{ flex: 1, minWidth: 220 }}
          items={[
            { key: 'execute', icon: <SendOutlined />, label: <Link to="/execute">ביצוע הפקדה</Link> },
            { key: 'investigate', icon: <FileSearchOutlined />, label: <Link to="/investigate">תחקור</Link> },
          ]}
        />
        <Tooltip title="סימולציה של זהות העובד — אינה מנגנון אבטחה">
          <Space>
            <UserOutlined style={{ color: '#fff' }} />
            <Select
              placeholder="בחר עובד"
              style={{ minWidth: 200 }}
              value={employee?.id}
              onChange={setEmployeeId}
              options={employees.map((e) => ({ value: e.id, label: `${e.name} · ${e.role}` }))}
            />
          </Space>
        </Tooltip>
      </Header>
      <Content style={{ padding: '24px clamp(16px, 3vw, 48px)' }}>
        <Routes>
          <Route path="/execute" element={<ExecutePage />} />
          <Route path="/investigate" element={<InvestigatePage />} />
          <Route path="*" element={<Navigate to="/execute" replace />} />
        </Routes>
      </Content>
      <Footer style={{ textAlign: 'center' }}>
        <Typography.Text type="secondary">כל הנתונים סינתטיים · אין חיבור למערכת בנקאית אמיתית</Typography.Text>
      </Footer>
    </Layout>
  )
}
